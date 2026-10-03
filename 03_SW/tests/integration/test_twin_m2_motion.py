"""M2 motion behaviour of A's firmware in the lock-step twin (virtual time, deterministic).

Gated on the INFO feature bits: a test runs only when A's build reports the feature it needs (MOTION, HOMING,
AFE, DRV_SIGNALS); until then it is skipped with that reason, so the module activates by itself when A's M2
code lands. PC side = TwinLink (ref_codec oracle, tests only). Expected values come from the ICD, the check
vectors and motion_vectors.json (single generator), never from A's code.

Verifies: FW-HOM-001, FW-MOT-003, FW-MOT-004, SAF-FW-002, SAF-FW-003, SAF-FW-005, SAF-FW-008, SAF-FW-013,
          SAF-FW-023 (D-30/D-31), IF-011 (stop path with a running move: the 4 cases skipped in M1)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import gen_params
import ref_codec as rc
from twin import Twin, TwinLink

pytestmark = pytest.mark.twin

VEC = Path(gen_params.__file__).resolve().parent / "vectors"
MOTION = {c["name"]: c for c in json.loads((VEC / "motion_vectors.json").read_text(encoding="utf-8"))["cases"]}
PD = gen_params.load()
PID = {p.key: p.id for p in PD.params}
PTYPE = {p.key: p.type for p in PD.params}
DEF = {p.key: p.default for p in PD.params}
TICK_US = 1e6 / 90e6                                   # one TIM2 tick in µs


def needs(link: TwinLink, *features: str) -> None:
    info = link.cmd("GET_INFO")["info"]
    missing = [f for f in features if f not in info["features"]]
    if missing:
        pytest.skip(f"A's FW build reports feature bit(s) {missing} = 0 (M2 in progress)")


def setp(link: TwinLink, key: str, value) -> dict:
    r = link.cmd("SET_PARAM", {"id": PID[key], "type": PTYPE[key], "value": value})
    assert r["status"] == "OK", (key, r)
    return r


def status(link: TwinLink) -> dict:
    r = link.cmd("GET_STATUS")
    assert r["status"] == "OK", r
    return r["board_status"]


def events(link: TwinLink, code: str) -> list[dict]:
    link.poll()
    return [e for e in link.events() if e["code"] == code]


def run(tw: Twin, link: TwinLink, ms: float) -> None:
    """Advance virtual time like a connected PC: a PING heartbeat every 150 ms (ICD §9.2), so the FW link
    watchdog (1 s while moving) never trips during a test."""
    end = tw.now_us + ms * 1000
    while tw.now_us < end:
        link.send("PING")
        tw.advance_us(min(150_000.0, end - tw.now_us))
    link.poll()


def enable(tw: Twin, link: TwinLink) -> None:
    r = link.cmd("ENABLE")
    assert r["status"] == "OK", r
    run(tw, link, r.get("settle_ms", DEF["motion.ena_settle_ms"]) + 20)
    assert status(link)["motion_state"] == "IDLE"


def wait_done(tw: Twin, link: TwinLink, timeout_ms: float, n_before: int = 0) -> dict:
    t_end = tw.now_us + timeout_ms * 1000
    while tw.now_us < t_end:
        run(tw, link, 100)
        done = events(link, "MOVE_DONE")
        if len(done) > n_before:
            return done[-1]
    raise AssertionError(f"no MOVE_DONE within {timeout_ms} ms")


def home(tw: Twin, link: TwinLink) -> None:
    n0 = len(events(link, "MOVE_DONE"))
    r = link.cmd("HOME", {"flags": 0})
    assert r["status"] == "OK", r
    d = wait_done(tw, link, 30_000, n0)
    assert d["arg"] == rc.MOVE_DONE_REASON.index("TARGET") and d["value"] == 0
    assert "HOMED" in status(link)["flags"]


def rises(tw: Twin, since_us: float) -> list[float]:
    e = tw.act("query", what="edges", since_us=since_us)["edges"]
    return [x["t_us"] for x in e if x["pin"] == "PUL" and x["level"] == 1]


@pytest.fixture
def ready(twin):
    """Precondition R-lite: booted, ENABLE + settle, HOME done (FW_test_plan §1.4)."""
    link = TwinLink(twin)
    twin.advance_ms(5)
    needs(link, "MOTION", "HOMING")
    link.cmd("STREAM_STOP")
    enable(twin, link)
    home(twin, link)
    return twin, link


# ================================================================================ homing and moves
@pytest.mark.req("FW-HOM-001", "D-29")
def test_home_at_start_edge(ready):
    """HOME only at START (D-29b): the captured START edge lies at x = -home.offset_um."""
    tw, link = ready
    world = tw.act("query", what="world")
    st = status(link)
    assert st["pos_um"] == 0 and "HOMED" in st["flags"] and st["home_phase"] == "DONE"
    edge_world = tw.world["start_switch_um"]
    assert world["x_um_true"] - edge_world == pytest.approx(DEF["home.offset_um"], abs=2 * 1000 / 800)


@pytest.mark.req("FW-MOT-003", "FW-MOT-004")
def test_move_abs_periods_match_motion_vectors(ready):
    """MOVE_ABS 10 mm at the defaults = motion_vectors 'default_move_10mm': every PUL period within ±1 tick."""
    tw, link = ready
    case = MOTION["default_move_10mm"]
    t0 = tw.now_us
    r = link.cmd("MOVE_ABS", {"target_um": 10_000, "v_um_s": case["v_um_s"], "a_um_s2": case["a_um_s2"]})
    assert r["status"] == "OK", r
    d = wait_done(tw, link, 2_000, len(events(link, "MOVE_DONE")))
    assert d["arg"] == rc.MOVE_DONE_REASON.index("TARGET") and d["value"] == 10_000
    t = rises(tw, t0)
    assert len(t) == case["n_steps"]
    got = [(b - a) / TICK_US for a, b in zip(t, t[1:])]
    want = case["periods"][1:]                         # rise-to-rise = period of the following step
    worst = max(abs(g - w) for g, w in zip(got, want))
    assert worst <= case["tolerance"]["period_ticks"] + 0.2, worst


# ================================================================================ stops with a running move
def _start_long_move(tw: Twin, link: TwinLink) -> float:
    r = link.cmd("MOVE_ABS", {"target_um": 200_000, "v_um_s": 10_000, "a_um_s2": 0})
    assert r["status"] == "OK", r
    run(tw, link, 300)                                  # cruise reached (10 mm/s, 100 mm/s^2 -> 0.1 s)
    assert status(link)["motion_state"] == "MOVE_ABS"
    return tw.now_us


@pytest.mark.req("SAF-FW-002", "IF-011")
@pytest.mark.parametrize("name,fields", [("HALT", {}), ("STOP", {"mode": 0})])
def test_immediate_stop_sniffer_with_running_move(ready, name, fields):
    """M2 part of the M1-skipped case: last byte of HALT / STOP 0 -> no further PUL edge <= 2 ms, and the stop
    primitive runs before the response is on the wire (ICD §2.4, §9.4)."""
    tw, link = ready
    _start_long_move(tw, link)
    at = (int(tw.now_us // 1000) + 3) * 1000 + 550.0    # mid-tick phase
    seq = link.send(name, fields, at_us=at)
    tw.advance_ms(20)
    link.poll()
    rx = [w for w in tw.wire_log if w["dir"] == "rx" and w["type"] == rc.CMD[name] and w["seq"] == seq][-1]
    tx = [w for w in tw.wire_log if w["dir"] == "tx" and w["type"] == rc.CMD[name] | 0x80 and w["seq"] == seq][0]
    after = rises(tw, rx["last_us"])
    assert not after or after[-1] - rx["last_us"] <= 2_000
    calls = [c for c in tw.act("query", what="seam_log", since_us=rx["last_us"])["seam_log"]
             if c["call"] in ("hal_step_stop_now", "hal_step_abort")]
    assert calls and calls[0]["t_us"] - rx["last_us"] <= 2_000 and calls[0]["t_us"] <= tx["first_us"]


@pytest.mark.req("SAF-FW-003", "IF-011")
@pytest.mark.parametrize("name,fields", [("STOP", {"mode": 1}), ("PAUSE", {})])
def test_controlled_stop_with_running_move(ready, name, fields):
    """STOP 1 / PAUSE: deceleration starts <= 2 ms after the last byte; afterwards periods never shorten."""
    tw, link = ready
    _start_long_move(tw, link)
    at = (int(tw.now_us // 1000) + 3) * 1000 + 300.0
    seq = link.send(name, fields, at_us=at)
    run(tw, link, 500)
    rx = [w for w in tw.wire_log if w["dir"] == "rx" and w["type"] == rc.CMD[name] and w["seq"] == seq][-1]
    t = rises(tw, rx["last_us"] - 5_000)
    per = [b - a for a, b in zip(t, t[1:])]
    cruise = 1e6 / (10_000 * 800 / 1000)                # 125 µs at 10 mm/s
    k = next(i for i, x in enumerate(t) if x > rx["last_us"] + 2_000)
    assert per[k - 1] > cruise + TICK_US                # already decelerating 2 ms after the trigger
    assert all(b >= a - 2 * TICK_US for a, b in zip(per[k - 1:], per[k:]))
    assert status(link)["motion_state"] == "IDLE"
    if name == "PAUSE":
        assert "PAUSED" in status(link)["status"]


# ================================================================================ PAUSE / RESUME (D-30, D-31)
@pytest.mark.req("SAF-FW-023", "D-30", "D-31")
def test_pause_blocks_in_flight_move_then_resume(ready):
    """A MOVE_ABS in flight right after PAUSE is refused (BLOCK PAUSED); RESUME clears PAUSED, then the target
    is re-issued and accepted."""
    tw, link = ready
    _start_long_move(tw, link)
    t = (int(tw.now_us // 1000) + 2) * 1000
    link.send("PAUSE", {}, at_us=t)
    s_mv = link.send("MOVE_ABS", {"target_um": 200_000, "v_um_s": 10_000, "a_um_s2": 0}, at_us=t + 1_000)
    run(tw, link, 500)
    r = link.find("MOVE_ABS", s_mv)
    assert r and r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("PAUSED"))
    assert link.cmd("RESUME")["status"] == "OK"
    assert "PAUSED" not in status(link)["status"]
    assert link.cmd("MOVE_ABS", {"target_um": 200_000, "v_um_s": 10_000, "a_um_s2": 0})["status"] == "OK"


@pytest.mark.req("SAF-FW-023", "D-31")
def test_resume_refused_after_halt_race(ready):
    """HALT (Pause/Break key) latched just before RESUME: RESUME -> E_STATE HALT, PAUSED and HALT stay."""
    tw, link = ready
    _start_long_move(tw, link)
    t = (int(tw.now_us // 1000) + 2) * 1000
    link.send("PAUSE", {}, at_us=t)
    link.send("HALT", {}, at_us=t + 200_000)
    s_rs = link.send("RESUME", {}, at_us=t + 200_500)
    run(tw, link, 400)
    r = link.find("RESUME", s_rs)
    assert r and r["status"] == "E_STATE" and r["detail"] == 1 << rc.BLOCK.index("HALT")
    st = status(link)
    assert "HALT" in st["flags"] and "PAUSED" in st["status"]


# ================================================================================ safety reactions
@pytest.mark.req("SAF-FW-005", "D-11")
def test_estop_during_move(ready):
    """E-stop sense opens while moving: no PUL edge later than 100 µs, ENA disabled <= 1 ms, ESTOP latched,
    HOMED and VALID cleared (ICD §6.2)."""
    tw, link = ready
    _start_long_move(tw, link)
    t = tw.now_us + 1_237.0
    tw.at(int(t * 1000), lambda: tw.act("estop", open=True), "estop")
    run(tw, link, 50)
    after = rises(tw, t)
    assert not after or after[0] - t <= 100.0
    ena = [x for x in tw.act("query", what="edges", since_us=t)["edges"] if x["pin"] == "ENA"]
    assert ena and ena[0]["t_us"] - t <= 1_000
    st = status(link)
    assert "ESTOP" in st["flags"] and "HOMED" not in st["flags"] and "VALID" not in st["flags"]
    assert any(e["arg"] == rc.STOP_CAUSE["ESTOP"] for e in events(link, "STOPPED"))


@pytest.mark.req("SAF-FW-013")
def test_limit_end_stops_and_blocks_toward(ready):
    """END switch inside the travel: the move stops at the edge, LIMIT_END latched; toward refused, away OK."""
    tw, link = ready
    tw.act("limit", name="end", position_um=50_000)
    r = link.cmd("MOVE_ABS", {"target_um": 100_000, "v_um_s": 5_000, "a_um_s2": 0})
    assert r["status"] == "OK", r
    d = wait_done(tw, link, 30_000, len(events(link, "MOVE_DONE")))
    assert d["arg"] == rc.MOVE_DONE_REASON.index("STOPPED")
    assert any(e["arg"] == rc.STOP_CAUSE["LIMIT_END"] for e in events(link, "STOPPED"))
    r = link.cmd("MOVE_ABS", {"target_um": 100_000, "v_um_s": 5_000, "a_um_s2": 0})
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("LIMIT"))
    assert link.cmd("MOVE_ABS", {"target_um": 10_000, "v_um_s": 5_000, "a_um_s2": 0})["status"] == "OK"


@pytest.mark.req("SAF-FW-008", "D-12")
def test_load_limit_trip_with_spring_specimen(ready):
    """A spring specimen loads the cell while moving: the first sample beyond safety.load_raw_max stops the
    axis (no PUL edge > 200 µs after that sample's DRDY) and latches LOAD_LIMIT."""
    tw, link = ready
    needs(link, "AFE")
    tw.act("specimen", kind="spring", k_n_per_mm=200.0, x_contact_um=20_000)
    setp(link, "safety.load_raw_max", 400_000)
    link.cmd("STREAM_START")
    r = link.cmd("MOVE_ABS", {"target_um": 100_000, "v_um_s": 2_000, "a_um_s2": 0})
    assert r["status"] == "OK", r
    wait_done(tw, link, 60_000, len(events(link, "MOVE_DONE")))
    trip = [e for e in events(link, "FAULT_SET") if e["arg"] == rc.FAULTS.index("LOAD_LIMIT")]
    assert trip and trip[0]["value"] > 400_000
    conv = [c for c in tw.act("query", what="conversions")["conversions"] if c["raw"] > 400_000]
    assert conv
    t_drdy = conv[0]["t_us"]
    after = rises(tw, t_drdy)
    assert not after or after[0] - t_drdy <= 200.0
    assert "LOAD_LIMIT" in status(link)["status"]
