"""Validator E - M2 twin suite: step generation, ramps, moves, stops (PRE-WRITTEN, gated).

Runs against Implementer A's unmodified FW in the Integrator's twin once GET_INFO reports MOTION (+HOMING);
until then every test SKIPS with "M2 pending". Oracles: vectors/motion_vectors.json (Integrator, read in
place), val_oracles/ramp_ref.py (validator, independent; agrees with the vectors case by case,
test_val_m2_oracles.py), ICD v0.5 §5.4/§6.2/§6.5, SRS v0.5.1.

Twin limits (plan §1.5): ISR bodies take no virtual time; the position counter is the twin's model
counter (counting integrity on silicon = HG-09); edge times are ns-rounded (periods reconstructed per
interval).

Verifies: FW-MOT-001, FW-MOT-002, FW-MOT-003, FW-MOT-004, FW-MOT-005, SAF-FW-002, SAF-FW-003, SAF-FW-004,
          FW-NVM-003, IF-005
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

import ramp_ref as rr
import ref_codec as rc
import vhelp_m2 as m

VEC = Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors"
SCN_640 = {"schema": "bird.bend.simscenario", "version": 1, "params": {"motion.steps_per_mm": 640.0}}


def _mv_case(name: str) -> dict:
    mv = json.loads((VEC / "motion_vectors.json").read_text(encoding="utf-8"))
    return next(c for c in mv["cases"] if c["name"] == name)


@pytest.fixture
def tw640(twin_exe, tmp_path):
    from twin import Twin
    t = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "run640", scenario=SCN_640)
    yield t
    t.close()


# --------------------------------------------------------------------------- FW-MOT-003 / -004
def test_tvm_move_periods_on_the_wire(tw640):
    """TC-FW-MOT-003-01 (T part) / TC-FW-MOT-004-01: R4 TV-M 100 mm move at 640 spm, 20 mm/s, 100 mm/s²."""
    from vhelp import V
    v = V(tw640)
    m.need(v, "MOTION", "HOMING")
    m.set_ok(v, "motion.steps_per_mm", 640.0)
    m.ready(v, x_um=None)
    t0 = v.tw.now_us
    md = m.move_abs(v, 100_000, 20_000, 100_000)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("TARGET") and md["value"] == 100_000 and md["value2"] == 64_000
    start = m.seam(v, "hal_step_start", t0)[0]["t_us"]
    p = m.periods_from_edges(m.edges(v, "PUL", t0), start)
    case = _mv_case("tvm_trapezoid")
    ok, msg = rr.check_periods(p, case["periods"])            # ±1 tick each, ±floor(N/1000) total
    assert ok, msg
    assert abs(sum(p) / m.F_TICK - rr.plan_trapezoid(64000, 12800.0, 64000.0, 64000.0)["t"]) <= 0.001  # ± 1 ms


def test_move_abs_busy_and_zero_length(v):
    """TC-FW-MOT-004-01: second MOVE_ABS while moving -> E_BUSY 1; target = position -> MOVE_DONE at once."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=10_000)
    m.move_abs(v, 20_000, 5_000, wait=False)
    v.advance(50)
    r = v.cmd("MOVE_ABS", {"target_um": 30_000, "v_um_s": 5_000, "a_um_s2": 0})
    assert (r["status"], r["detail"]) == ("E_BUSY", 1), r
    m.wait_until(v, lambda: m.state(v) == "IDLE", 10_000)
    t0 = v.tw.now_us
    n0 = m.n_events(v)
    v.ok("MOVE_ABS", {"target_um": 20_000, "v_um_s": 5_000, "a_um_s2": 0})
    md = m.wait_event(v, "MOVE_DONE", n0, 50)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("TARGET") and md["value"] == 20_000
    assert m.rising_count(v, t0) == 0


def test_same_seq_move_twice_no_double_motion(v):
    """TC-IF-005-01 (M2 part, carried condition E-C2): the same MOVE_ABS frame (same SEQ) twice."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=10_000)
    t0 = v.tw.now_us
    seq = v.link.send("MOVE_ABS", {"target_um": 12_000, "v_um_s": 5_000, "a_um_s2": 0})
    v.advance(20)
    v.link.send("MOVE_ABS", {"target_um": 12_000, "v_um_s": 5_000, "a_um_s2": 0}, seq=seq)
    m.run(v, 3000)
    resp = [rc.decode_frame(f) for f in v.link.frames if f.type == (rc.CMD["MOVE_ABS"] | rc.RESP_BIT) and f.seq == seq]
    assert len(resp) == 2 and resp[0]["status"] == "OK" and resp[1]["status"] in ("E_BUSY", "OK"), resp
    assert m.rising_count(v, t0) == 1600                        # 2 mm at 800 spm, executed once


def test_save_while_moving_busy(v):
    """TC-FW-NVM-003-01 (M2 part, E-C2): SAVE/LOAD/DEFAULT while moving -> E_BUSY 1, no flash write."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=10_000)
    w0 = v.tw.act("query", what="flash")["writes"]
    m.move_abs(v, 60_000, 10_000, wait=False)
    v.advance(100)
    for c in ("SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS"):
        r = v.cmd(c)
        assert (r["status"], r["detail"]) == ("E_BUSY", 1), (c, r)
    assert v.tw.act("query", what="flash")["writes"] == w0


# --------------------------------------------------------------------------- FW-MOT-001
@pytest.mark.parametrize("high_ns, low_ns, rate_hz", [
    (None, None, None),                 # dict 6 defaults (D-45 e): 12.5 + 12.5 µs, 40 kHz
    (10_000, 10_000, 50_000),           # explicit old setting, still legal under H3 (vector rule_h3_old_rate_ok)
])
def test_pulse_timing_at_cap_and_dir_setup(twin_exe, tmp_path, high_ns, low_ns, rate_hz):
    """TC-FW-MOT-001-02: step-rate cap (spm 2000 -> v_limit = rate / 2), widths and DIR setup on reversals."""
    from twin import Twin
    from vhelp import V
    t = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "r",
             scenario={"schema": "bird.bend.simscenario", "version": 1, "params": {"motion.steps_per_mm": 2000.0}})
    try:
        v = V(t)
        m.need(v, "MOTION", "HOMING")
        if rate_hz is not None:                         # widths first, then the rate (hard rule H3 on every write)
            m.set_ok(v, "motion.pulse_high_ns", high_ns)
            m.set_ok(v, "motion.pulse_low_min_ns", low_ns)
            m.set_ok(v, "motion.max_step_rate_hz", rate_hz)
        high_ns, low_ns, rate_hz = (int(v.get(k)) for k in ("motion.pulse_high_ns", "motion.pulse_low_min_ns",
                                                             "motion.max_step_rate_hz"))
        m.set_ok(v, "motion.steps_per_mm", 2000.0)
        m.ready(v, x_um=20_000, v_um_s=5_000)
        v_cap = rate_hz * 1000 // 2000
        assert v.status()["v_limit_um_s"] == min(v_cap, 30_000)
        t0 = v.tw.now_us
        m.move_abs(v, 40_000, v_cap)
        m.move_abs(v, 20_000, v_cap)                    # reversal
        pul = m.edges(v, "PUL", t0)
        hi, lo = m.pulse_widths(pul)
        pt = rr.pulse_timing(high_ns, low_ns, rate_hz, int(v.get("motion.dir_setup_us")))
        assert min(hi) >= pt.pw_ticks - 1 and max(hi) <= pt.pw_ticks + 1, (min(hi), max(hi))
        assert min(lo) >= m.ticks(low_ns / 1000) - 1, min(lo)
        assert min(m.periods_from_edges(pul, t0)[1:]) >= pt.c_min_ticks - 1
        dirs = m.edges(v, "DIR", t0)
        assert dirs, "reversal must toggle DIR"
        for d in dirs:
            nxt = [e for e in pul if e["level"] == 1 and e["t_us"] > d["t_us"]]
            assert nxt and m.ticks(nxt[0]["t_us"] - d["t_us"]) >= pt.dir_setup_ticks - 1
            prev = [e for e in pul if e["t_us"] <= d["t_us"]]
            assert not prev or prev[-1]["level"] == 0, "DIR changed while a pulse was high"
    finally:
        t.close()


# --------------------------------------------------------------------------- SAF-FW-004 / 002
STOPS = int(os.environ.get("VAL_STOPS", "200"))        # 10 000 at the M2 gate run (plan §5.2)


def test_random_stops_count_integrity(v):
    """TC-SAF-FW-004-01 (T): random stop source and phase; rising PUL edges == Δpos_steps (±1 only with
    POS_UNCERTAIN); no pulse shorter than pulse_high (no runt). Twin evidence covers the core's halt-path
    choice, not silicon counting (HG-09)."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=50_000)
    rng = random.Random(int(os.environ.get("VAL_SEED", "1") or 1))
    pt = rr.pulse_timing(10_000, 10_000, 50_000, 20)
    for i in range(STOPS):
        v.tw.act("query", what="edges", clear=True)          # keep the per-stop scans O(1) (10 000-stop gate run)
        v.tw.act("query", what="seam_log", clear=True)
        s0 = v.status()["pos_steps"]
        t0 = v.tw.now_us
        target = 50_000 + rng.choice([-1, 1]) * 30_000
        m.move_abs(v, target, rng.choice([1_000, 10_000, 30_000]), wait=False)
        v.advance(rng.uniform(20, 400))
        src = rng.choice(["STOP0", "STOP1", "HALT"])
        if src == "HALT":
            v.ok("HALT")
        else:
            v.ok("STOP", {"mode": 0 if src == "STOP0" else 1})
        m.wait_until(v, lambda: m.state(v) == "IDLE", 5_000, 2.0)
        s = v.status()
        n = m.rising_count(v, t0)
        d = abs(s["pos_steps"] - s0)
        unc = "POS_UNCERTAIN" in s["status"]
        assert n == d or (unc and abs(n - d) <= 1), (i, src, n, d, unc)
        hi, _ = m.pulse_widths(m.edges(v, "PUL", t0))
        assert not hi or min(hi) >= pt.pw_ticks - 1, (i, src, min(hi))
        if src == "HALT":
            v.ok("HALT_CLEAR")
        m.move_abs(v, 50_000, 30_000)


def test_stop_frame_last_byte_to_last_edge(v):
    """TC-SAF-FW-002-01 (T, PC paths): STOP 0 / HALT last byte at random phases vs the 1 kHz tick;
    last PUL edge <= 2 ms after the last byte (wire_log last_us)."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=50_000)
    rng = random.Random(7)
    worst = 0.0
    for i in range(50):
        m.move_abs(v, 250_000 if i % 2 == 0 else 50_000, 30_000, wait=False)
        v.advance(300 + rng.uniform(0, 1))
        name = "HALT" if i % 3 == 0 else "STOP"
        seq = v.link.send(name, {} if name == "HALT" else {"mode": 0})
        v.advance(10)
        rx = [w for w in v.wire("rx") if w["seq"] == seq and w["type"] == rc.CMD[name]]
        last_byte = rx[-1]["last_us"]
        pul = [e for e in m.edges(v, "PUL", last_byte - 5_000)]
        last_edge = max((e["t_us"] for e in pul), default=last_byte)
        worst = max(worst, last_edge - last_byte)
        if name == "HALT":
            v.ok("HALT_CLEAR")
        m.wait_until(v, lambda: m.state(v) == "IDLE", 5_000)
    assert worst <= 2_000.0, f"worst last-byte -> last PUL edge {worst:.1f} µs"


# --------------------------------------------------------------------------- SAF-FW-003
@pytest.mark.parametrize("v_um_s", [500, 1_000, 10_000])     # P > 2 ms / 1…2 ms / < 1 ms at 800 spm
def test_controlled_stop_commit_and_profile(v, v_um_s):
    """TC-SAF-FW-003-01: STOP mode 1 -> deceleration commit <= 2 ms (seam_log set_period_now / first
    lengthened period / clean halt), later periods non-decreasing, stop distance = v²/(2 a_stop) ± 1 step;
    clean halt only if ICD §6.5 allows it (TC-SAF-FW-003-02)."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=20_000)
    m.move_abs(v, 200_000, v_um_s, wait=False)
    m.run(v, 3_000)
    s0 = v.status()["pos_steps"]
    t_trig = v.tw.now_us
    v.ok("STOP", {"mode": 1})
    m.wait_until(v, lambda: m.state(v) == "IDLE", 10_000)
    vs = v_um_s * 800 / 1000
    a_stop = 1_000_000 * 800 / 1000
    steps = v.status()["pos_steps"] - s0
    cruise = rr.F_TICK_TARGET / vs                                # running period before the trigger (ticks)
    clean = rr.clean_halt_allowed(cruise / rr.F_TICK_TARGET, vs, a_stop)
    # Falling PUL edges (= timer updates) from the last one before the trigger: F[0] < t_trig <= F[1] ...
    fall_all = [e["t_us"] for e in m.edges(v, "PUL", t_trig - 10 * cruise / 90) if e["level"] == 0]
    before = [f for f in fall_all if f < t_trig]
    F = ([before[-1]] if before else [t_trig]) + [f for f in fall_all if f >= t_trig]
    per = [m.ticks(b - a) for a, b in zip(F, F[1:])]            # per[i-1] = period i (F[i-1] -> F[i])
    if clean:
        assert steps <= 1 and "POS_UNCERTAIN" not in v.status()["status"], steps
        stops = m.seam(v, "hal_step_stop_now", t_trig)
        assert stops and stops[0]["t_us"] - t_trig <= 2_000.0 + 1e-3, stops[:1]
        return
    k = next(i for i, c in enumerate(per, start=1) if c > cruise + 1)   # first lengthened period
    if k >= 2:
        # ISR path: the preload of period k is written by the update ISR at the start of period k-1 (= F[k-2])
        commit = max(t_trig, F[k - 2])
    else:
        # stretch path: the running period was reprogrammed (hal_step_set_period_now)
        sn = [s for s in m.seam(v, "hal_step_set_period_now", t_trig)]
        assert sn, "running period lengthened without hal_step_set_period_now"
        commit = sn[0]["t_us"]
    assert commit - t_trig <= 2_000.0 + 1e-3, ("commit", commit - t_trig)
    decel = per[k - 1:]
    assert all(b + 1 >= a for a, b in zip(decel, decel[1:])), "decel periods must not decrease"
    # SRS SAF-FW-003: stop distance (from the deceleration commit) = v²/(2 a_stop) ± 1 step
    assert abs(len(decel) - rr.stop_distance_steps(vs, a_stop)) <= 1.0 + 1e-9, (len(decel), steps)


# --------------------------------------------------------------------------- FW-MOT-005
def test_jog_never_passes_soft_limit_and_bound(v):
    """TC-FW-MOT-005-01 (part): jog to the soft limit and to a bound (world truth), bound 0 honoured."""
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=5_000)
    n0 = m.n_events(v)
    assert m.jog(v, -2_000)["status"] == "OK"
    for _ in range(40):                     # refresh the dead-man
        v.advance(200)
        m.jog(v, -2_000)
        if m.events_since(v, n0, "MOVE_DONE"):
            break
    md = m.wait_event(v, "MOVE_DONE", n0, 2_000)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("SOFT_LIMIT") and md["value"] == 500       # soft_min
    m.set_ok(v, "limits.soft_min_um", -500)
    n0 = m.n_events(v)
    assert m.jog(v, -1_000, 0, 0)["status"] == "OK"           # bound 0 is a real position
    for _ in range(10):
        v.advance(200)
        m.jog(v, -1_000, 0, 0)
        if m.events_since(v, n0, "MOVE_DONE"):
            break
    md = m.wait_event(v, "MOVE_DONE", n0, 2_000)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("BOUND") and md["value"] == 0
    r = m.jog(v, -1_000, 0, 1_000)                           # bound behind the axis
    assert (r["status"], r["detail"]) == ("E_RANGE", 8), r
