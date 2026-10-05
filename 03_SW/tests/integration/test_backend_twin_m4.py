"""M4 integration (D-46): Implementer B's sequencer (backend) ⇄ A's firmware in the twin, one lock-step virtual time
(``twin_rig.Rig``), with the twin's M4 specimen model (tools/README ``specimen``: stiffness, contact point, side
pull / push / both, cubic non-linearity, relaxation, break at a force or a deflection with a residual fraction).

Every test starts from a calibrated (zero + 1 kg + 10 kg, R2 cell 3 285 counts/N) and tared session, homed, parked
at machine 20 mm = **test zero** (sequence ``travel_ref = "test"``), recording root in tmp. Flows (SRS v0.6.2,
SW_design §10–§11):

1. staircase TRAVEL sequence with a loop: MOVE_ABS targets exactly per plan; **VALID = 1 exactly over the capture
   windows** (FW rule: a DATA frame is VALID iff ``t_on ≤ t < t_off`` of a SET_VALID 1/0 pair), each window opened
   at t_reached + settle and lasting ``capture`` within one frame, dwell ``max(step_time, settle + capture)``,
   no motion inside a window (SW-SEQ-003/004);
2. LOAD steps on k = 50 N/mm with k_est = 40: MOVE_UNTIL_LOAD bound = soft limit, cmp, raw_stop per band; ≤ 10
   trims of ≤ 0.2 mm at 0.2 mm/s; window mean within tol; also on the **compression side** (push specimen,
   negative target: bound = soft minimum, cmp LE) (SW-SEQ-006, FW-MOT-006);
3. NOT_REACHED: specimen too soft — approach ends at the FW soft limit (MOVE_DONE BOUND) or at an enabled SW travel
   limit when nearer; step NOT_REACHED, sequence stopped, no trim, no SW-limit trip (SW-SEQ-006, D-32);
4. BREAK_DETECTED (load drop > 20 % of the running maximum) during a load approach (clean break) and during a
   TRAVEL step (break by deflection, 50 % residual): priority STOP ≤ 50 ms after the first frame of the drop,
   sequence stopped (SW-SEQ-007);
5. pause / resume mid-step (GUI PAUSE during a move and during a capture; PAUSE button + RESUME_REQUEST): VALID 0
   during the pause, the interrupted step re-issued with the same absolute target, the sequence finishes
   (SW-SEQ-007, SW-STOP-004, D-30/D-31);
6. controls: stop → STOP mode 1, sequence STOPPED; abort → HALT, ABORTED; the global Pause/Break HALT ends it as
   well; nothing is sent by the sequence afterwards (SW-SEQ-007, SW-STOP-003);
7. ALM during a sequence → controlled STOP (mode 1), ended "driver alarm", no retry (SW-SEQ-007, D-33 c);
8. link loss (PC → FW silent: FW link watchdog) during a move and during a capture: sequence ends, VALID never
   re-asserted, no command of the sequence afterwards (SW-STOP-003, SAF-SW-003, SAF-FW-012);
9. report: CSV + JSON + HTML exist; the report rebuilt offline from the recording equals the one built online at
   the sequence end; the raw window statistics equal the Integrator's recomputation from the DATA frames on the
   wire (ICD §7.6 window rule) (SW-REP-001…003).

xfail (run=False) while B's ``bend_stand.core.sequencer`` is absent; run-time xfail while an API is still B's M4
stub (NotImplementedError / NOT_IMPLEMENTED). Flip = automatic when B's code lands.

Verifies: SW-SEQ-003, SW-SEQ-004, SW-SEQ-006, SW-SEQ-007, SW-STOP-003, SW-STOP-004, SW-REP-001, SW-REP-002,
          SW-REP-003, FW-MOT-006, SAF-SW-003, SYS-008
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pytest

import seq_support as ss
from test_backend_twin_m3 import NOISE, OFFSET, enable_home, load_calibration, tare

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.core.backend", "Backend"),
              pytest.mark.needs_b("bend_stand.core.sequencer")]

ZERO_MM = 20.0                      # test zero (machine mm) for every sequence
FRAME, SLACK = ss.FRAME_US, ss.SLACK_US
# Integrator findings against B's first M4 delivery, fixed by B and flipped (D-47): SWC-M4-02 break reported as SLIP
# (guard order), SWC-M4-03 link loss during a capture ended ERROR + VALID left 1 (with A's D-47 a FW change),
# SWC-M4-04 StepResult.t_reached_s None.


@dataclass
class M4:
    rig: object
    x0_um: float                    # world x of machine 0
    K: float                        # N per count (active load calibration)
    tare_raw: float
    tmp: Path
    rec: Path

    def world(self, machine_mm: float) -> float:
        return self.x0_um + machine_mm * 1000.0

    def force(self, raw: float) -> float:
        return self.K * (raw - self.tare_raw)

    def dev(self, world_us: float) -> int:
        """Device time (µs) of a world time (µs) — no MCU reset within a test."""
        return self.rig.tw.fw_t_us(int(round(world_us * 1000)))

    def load(self, doc: dict):  # noqa: ANN201
        return ss.load_sequence(self.rig.be, doc, self.tmp / "seq")


@pytest.fixture
def m4(twin_exe, tmp_path, monkeypatch):
    from twin_rig import Rig

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    rig = Rig(twin_exe, tmp_path / "twin", recordings=tmp_path / "rec")
    try:
        if reason := ss.sequencer_stub(rig.be):
            pytest.xfail(reason)
        rig.act("load_offset", counts=OFFSET)
        rig.act("afe", noise_counts=NOISE, rate_error=0.0)
        rig.connect()
        rig.advance(500)
        load_calibration(rig)
        tare(rig, 2.0)
        rig.result(rig.be.limits.recheck_async())
        enable_home(rig)
        out = rig.result(rig.be.motion.move_to(ZERO_MM, speed_mm_s=10.0), 20_000)
        assert "TARGET" in str(out), out
        rig.advance(200)
        assert rig.be.motion.set_test_zero() == pytest.approx(ZERO_MM, abs=0.002)
        w = rig.tw.act("query", what="world")
        x0 = w["x_um_true"] - w["pos_steps"] * 1000.0 / 800.0
        K = float(rig.be.calibrations.active_load()["fit"]["k_n_per_count"])
        tr = float(rig.be.status().tare.tare_raw)
        yield M4(rig, x0, K, tr, tmp_path, tmp_path / "rec")
    finally:
        rig.close()


def assert_finished(st) -> None:  # noqa: ANN001
    name, reason = ss.state_name(st), str(getattr(st, "end_reason", "") or "").upper()
    ok = name in ("FINISHED", "DONE", "COMPLETED") or (name in ("ENDED", "IDLE") and any(
        w in reason for w in ("FINISH", "COMPLETE", "DONE")))
    assert ok, f"sequence did not finish: {ss.flatten(st)}"


def assert_ended(st, *words: str, not_finished: bool = True) -> None:  # noqa: ANN001
    text = ss.outcome_text(st)
    assert all(w.upper() in text for w in words), f"expected {words} in the sequence outcome: {ss.flatten(st)}"
    if not_finished:
        assert ss.state_name(st) not in ("FINISHED", "DONE", "COMPLETED"), ss.flatten(st)


def results(m: M4) -> list:  # noqa: ANN201
    """Step results of the current / last run (SW_design §15.5f B6-06/B6-08)."""
    return list(m.rig.be.sequencer.results())


def flags_of(r) -> set[str]:  # noqa: ANN001
    return set(getattr(r, "flags", ()) or ())


def wait_for(m: M4, pred, timeout_ms: float, step_ms: float = 1.0) -> None:  # noqa: ANN001
    assert m.rig.run_until(pred, timeout_ms, step_ms), "condition not reached in virtual time"


def move_cmds(m: M4, since_us: float, target_mm: float | None = None) -> list[dict]:
    cs = ss.rx_cmds(m.rig.tw, since_us, ("MOVE_ABS",))
    if target_mm is not None:
        cs = [c for c in cs if c["fields"]["target_um"] == round(target_mm * 1000)]
    return cs


# ============================================================================================ 1 staircase / VALID
def check_windows(m: M4, t0_us: float, settle_s: float, capture_s: float, n_expected: int,
                  step_time_s: list[float] | None = None) -> list[dict]:
    """SW-SEQ-003/004 on the wire: windows = SET_VALID 1/0 pairs (applied device times); DATA VALID iff inside a
    window (FW rule); **every VALID = 1 frame inside its planned window [t_reached + settle, + capture]** (t_reached
    = the preceding MOVE_DONE TARGET); window start and end, and the next step's command at t_reached + max(step
    time, settle + capture), each within ±1 frame (+ wire / 1 ms lock-step slack) of plan; no motion frame or
    command inside a window."""
    tw = m.rig.tw
    sv = [s for s in ss.set_valid_windows(tw, t0_us)]
    assert all(s["status"] == "OK" for s in sv), sv
    pairs = []
    for on, off in zip(sv[0::2], sv[1::2]):
        assert (on["valid"], off["valid"]) == (1, 0), sv
        pairs.append((on["t_us"], off["t_us"]))
    assert len(sv) % 2 == 0 and len(pairs) == n_expected, f"{len(pairs)} windows, plan {n_expected}: {sv}"
    frames = ss.data(tw, t0_us)
    for d in frames:                                                        # VALID exactly over the windows
        inside = any(a <= d["t_us"] < b for a, b in pairs)
        assert ("VALID" in d["flags"]) == inside, (d, pairs)
        if inside:
            assert "MOVING" not in d["flags"], d
    assert len(ss.valid_runs(frames)) == n_expected
    done = [e for e in ss.events(tw, t0_us, ("MOVE_DONE",)) if e["arg"] == 0]          # reason TARGET
    cmds = [c for c in ss.rx_cmds(tw, t0_us) if c["name"] in ss.MOTION_CMDS]
    out = []
    for k, (a, b) in enumerate(pairs):
        t_r = max(e["t_us"] for e in done if e["t_us"] <= a)
        p0, p1 = t_r + settle_s * 1e6, t_r + (settle_s + capture_s) * 1e6
        assert abs(a - p0) <= FRAME + SLACK, ("capture start vs t_reached + settle", t_r, a)
        assert abs(b - p1) <= FRAME + SLACK, ("capture end vs t_reached + settle + capture", t_r, b)
        inside = [d for d in frames if a <= d["t_us"] < b]
        assert inside and all(p0 <= d["t_us"] <= p1 for d in inside), ("VALID frame outside the planned window",
                                                                       p0, p1, inside[0]["t_us"], inside[-1]["t_us"])
        nxt = [m.dev(c["first_us"]) for c in cmds if m.dev(c["first_us"]) > a]
        assert not [t for t in nxt if t < b], "motion command inside a capture window"
        if nxt:
            dwell = max(step_time_s[k] if step_time_s else 0.0, settle_s + capture_s) * 1e6
            assert abs(nxt[0] - (t_r + dwell)) <= FRAME + SLACK, ("step end vs t_reached + dwell", t_r, nxt[0])
        out.append({"t_reached": t_r, "t_on": a, "t_off": b})
    return out


@pytest.mark.req("SW-SEQ-002", "SW-SEQ-003", "SW-SEQ-004")
def test_staircase_travel_sequence_valid_exactly_over_capture_windows(m4):
    """5 → 10 → 15 mm (test) twice (loop) + return to 0, settle 0.5 s, capture 1 s, step time 2 s on the first
    level; spring 20 N/mm in contact from test 2 mm (non-zero, varying load in every window)."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=20.0, x_contact_um=m.world(ZERO_MM + 2.0))
    doc = ss.seq_doc("staircase", [
        ss.step("a", "travel", 5.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0, step_time_s=2.0, label="5 mm"),
        ss.step("b", "travel", 10.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0, label="10 mm"),
        ss.step("c", "travel", 15.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0, label="15 mm"),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0, settle_s=0.0, capture_s=0.0, label="return")],
        loops=[{"first": 0, "last": 2, "count": 2}])
    seq = m.load(doc)
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 90_000)
    assert_finished(st)
    targets = [c["fields"]["target_um"] for c in ss.rx_cmds(m.rig.tw, t0, ss.MOTION_CMDS)]
    assert targets == [round((ZERO_MM + x) * 1000) for x in (5, 10, 15, 5, 10, 15, 0)], targets
    assert all(c["fields"]["v_um_s"] == 5000 for c in move_cmds(m, t0))
    win = check_windows(m, t0, 0.5, 1.0, 6, step_time_s=[2.0, 0, 0, 2.0, 0, 0])
    nxt = [m.dev(c["first_us"]) for c in move_cmds(m, t0)][1]      # level 1: dwell = step time 2 s > 1.5 s
    assert nxt - win[0]["t_reached"] >= 2_000_000 - FRAME
    assert ss.events(m.rig.tw, t0, ("VALID_CLEARED",)) == []             # windows closed by the SW, not the FW


# ============================================================================================ 2 load steps
def load_step_checks(m: M4, t0: float, targets: list[float], tol: float, v_mm_s: float, k_est0: float,
                     bound_um: int, cmp: int) -> None:
    tw = m.rig.tw
    cmds = [c for c in ss.rx_cmds(tw, t0) if c["name"] in ("MOVE_UNTIL_LOAD", "MOVE_ABS", "SET_VALID")]
    frames = ss.data(tw, t0)
    muls = [i for i, c in enumerate(cmds) if c["name"] == "MOVE_UNTIL_LOAD"]
    assert len(muls) == len(targets), [c["name"] for c in cmds]
    for n, (i, f_t) in enumerate(zip(muls, targets)):
        mul = cmds[i]["fields"]
        assert mul["bound_um"] == bound_um and mul["cmp"] == cmp and mul["v_um_s"] == round(v_mm_s * 1000), mul
        f_stop = m.force(mul["raw_stop"])
        if n == 0:                                     # band = max(tol, k_est · v · 0.065 s) with the sequence k_est
            band = max(tol, k_est0 * v_mm_s * 0.065)
            assert f_stop == pytest.approx(f_t - math.copysign(band, f_t), abs=2 * m.K + 1e-6), (mul, f_stop)
        else:                                          # k_est from the approach OLS (≈ 50 N/mm, clamped)
            assert 0.065 * v_mm_s * 30 <= abs(f_t - f_stop) <= 0.065 * v_mm_s * 80 + tol, (mul, f_stop)
        j = next(k for k in range(i + 1, len(cmds)) if cmds[k]["name"] == "SET_VALID")
        trims = [cmds[k] for k in range(i + 1, j) if cmds[k]["name"] == "MOVE_ABS"]
        assert len(trims) <= 10, f"{len(trims)} trims for {f_t} N"
        for tr in trims:                                # ≤ 0.2 mm at 0.2 mm/s from the setpoint before the trim
            before = [d for d in frames if d["wire_us"] <= tr["first_us"]][-1]
            assert abs(tr["fields"]["target_um"] - before["setpoint_um"]) <= 201, (tr, before)
            assert tr["fields"]["v_um_s"] == 200, tr
        on, off = cmds[j], cmds[j + 1]
        assert on["fields"]["valid"] == 1 and off["name"] == "SET_VALID" and off["fields"]["valid"] == 0
        win = [d for d in frames if "VALID" in d["flags"] and on["first_us"] < d["wire_us"] <= off["last_us"] + FRAME]
        assert win and all("MOVING" not in d["flags"] for d in win)
        assert len({d["setpoint_um"] for d in win}) == 1                   # position frozen during capture
        f_mean = ss.mean([m.force(d["afe_raw"]) for d in win])
        assert abs(f_mean - f_t) <= tol, (f_t, f_mean, len(trims))


@pytest.mark.req("SW-SEQ-006", "FW-MOT-006")
def test_load_target_steps_reach_tolerance_within_10_trims(m4):
    """Spring k = 50 N/mm from test 5 mm; k_est = 40 (sequence); 100 N then 200 N, tol 2 N, approach 2 mm/s."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=m.world(ZERO_MM + 5.0))
    doc = ss.seq_doc("load steps", [
        ss.step("l1", "load", 100.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0, label="100 N"),
        ss.step("l2", "load", 200.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0, label="200 N"),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0, label="return")], k_est=40.0)
    seq = m.load(doc)
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 120_000)
    assert_finished(st)
    load_step_checks(m, t0, [100.0, 200.0], 2.0, 2.0, 40.0, bound_um=290_000, cmp=0)
    for r in [r for r in results(m) if r.uid in ("l1", "l2")]:
        assert "ON_TARGET" in flags_of(r), r
        iters = [int(f.rsplit("_", 1)[1]) for f in flags_of(r) if f.startswith("TRIM_ITER_")]
        assert iters and iters[0] <= 10, flags_of(r)
    w = m.rig.tw.act("query", what="world")
    assert w["x_um_true"] == pytest.approx(m.world(ZERO_MM), abs=2)          # returned to test zero


@pytest.mark.req("SW-SEQ-006", "FW-MOT-006")
def test_load_target_step_on_the_compression_side(m4):
    """Push specimen (twin M4 model, side 'push') from test −5 mm, k = 50 N/mm, target −100 N (pull_dir +1):
    approach toward the soft minimum with cmp LE, within tol in ≤ 10 trims."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=m.world(ZERO_MM - 5.0), side="push")
    doc = ss.seq_doc("push step", [
        ss.step("p1", "load", -100.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0, label="-100 N"),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0, label="return")], k_est=40.0)
    seq = m.load(doc)
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 60_000)
    assert_finished(st)
    load_step_checks(m, t0, [-100.0], 2.0, 2.0, 40.0, bound_um=500, cmp=1)


# ============================================================================================ 3 NOT_REACHED
@pytest.mark.req("SW-SEQ-006", "SW-SEQ-007", "SAF-SW-001")
@pytest.mark.parametrize("bound", ["soft_limit", "sw_travel_limit"])
def test_not_reached_at_the_bound(m4, bound):
    """Spring 1 N/mm from test 5 mm, target 100 N: soft maximum lowered to 60 mm (board RAM) — or an enabled SW
    travel maximum at 45 mm (nearer) — is reached first: MOVE_DONE BOUND at that bound, step NOT_REACHED, sequence
    stopped, no trim, no SW-limit trip."""
    import dataclasses

    m = m4
    rig = m.rig
    if bound == "soft_limit":
        rig.result(rig.be.config.write_and_verify_async({"limits.soft_max_um": 60_000}), 10_000)
        bound_um = 60_000
    else:
        cfg = rig.be.limits.get()
        issues = rig.be.limits.set(dataclasses.replace(cfg, travel_max_mm=45.0, travel_max_enabled=True))
        assert not [i for i in issues if "ERROR" in str(i.severity)], issues
        bound_um = 45_000
    rig.advance(300)
    rig.act("specimen", kind="spring", k_n_per_mm=1.0, x_contact_um=m.world(ZERO_MM + 5.0))
    seq = m.load(ss.seq_doc("not reached", [
        ss.step("l1", "load", 100.0, speed_mm_s=10.0, tol_n=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=10.0)], k_est=40.0))
    t0 = rig.tw.now_us
    ss.start_sequence(rig.be, seq)
    st = ss.run_sequence(rig, 60_000)
    assert_ended(st, "NOT_REACHED")
    muls = ss.rx_cmds(rig.tw, t0, ("MOVE_UNTIL_LOAD",))
    assert len(muls) == 1 and muls[0]["fields"]["bound_um"] == bound_um, muls
    md = ss.events(rig.tw, t0, ("MOVE_DONE",))
    assert [(e["arg"], e["value"]) for e in md] == [(2, bound_um)], md               # BOUND at the bound
    assert move_cmds(m, t0) == []                                                     # no trim, no return step
    assert [c for c in ss.rx_cmds(rig.tw, t0) if c["name"] == "SET_VALID" and c["fields"]["valid"] == 1] == []
    r = [r for r in results(m) if r.uid == "l1"][-1]
    assert "NOT_REACHED" in flags_of(r), r
    assert r.x_end_mm == pytest.approx(bound_um / 1000.0 - ZERO_MM, abs=0.002)        # sequence coordinate (test)
    assert r.f_end_n == pytest.approx(1.0 * (bound_um / 1000.0 - ZERO_MM - 5.0), abs=1.0)
    s = rig.be.status().safety
    assert s.sw_trip is None and not s.trips, ss.flatten(s)                          # not a SW-limit trip
    assert ss.events(rig.tw, t0, ("LIMIT_SET",)) == []


# ============================================================================================ 4 BREAK_DETECTED
def check_guard_stop(m: M4, t0: float, call: str = "specimen_break", flag: str = "BREAK_DETECTED") -> None:
    """Priority STOP ≤ 50 ms (the SAF-SW-001 per-frame budget) after the last byte of the first DATA frame sampled
    after the twin's specimen event; nothing is commanded afterwards; the step result carries the guard flag."""
    tw = m.rig.tw
    ev = [e for e in tw.seam_log if e["call"] == call and e["t_us"] >= t0]
    assert len(ev) == 1, ev
    t_b = ev[0]["t_us"]
    first_drop = next(d for d in ss.data(tw, t_b))                        # first frame sampled after the event
    stops = ss.rx_cmds(tw, t_b, ("STOP", "HALT"))
    assert stops, f"no STOP after {call}"
    assert stops[0]["first_us"] - first_drop["wire_us"] <= 50_000, (first_drop, stops[0])
    assert ss.no_motion_after(tw, stops[0]["first_us"]) == []
    assert any(flag in flags_of(r) for r in results(m)), [flags_of(r) for r in results(m)]


@pytest.mark.req("SW-SEQ-007")
def test_break_detected_during_a_load_approach(m4):
    """Spring 50 N/mm from test 5 mm breaks cleanly at 150 N (60 % of the 250 N target) while the approach runs
    toward the far soft limit: BREAK_DETECTED aborts at once (D-32), sequence stopped."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=m.world(ZERO_MM + 5.0), f_break_n=150.0)
    seq = m.load(ss.seq_doc("break load", [
        ss.step("l1", "load", 250.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0)], k_est=40.0))
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 60_000)
    assert_ended(st, "BREAK_DETECTED")
    check_guard_stop(m, t0)


@pytest.mark.req("SW-SEQ-007")
def test_slip_detected_during_a_load_approach(m4):
    """Grip slip (twin M4 model; the simulator's B6-17 extra): at 150 N the contact point moves 0.4 mm → the load
    drops 20 N = 6.7 % of the 300 N target (> 5 %: SLIP) but only 13 % of the running maximum (< 20 %: not a
    break) → SLIP guard: priority STOP, sequence stopped with reason SLIP."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=m.world(ZERO_MM + 5.0), slip_at_n=150.0,
              slip_mm=0.4)
    seq = m.load(ss.seq_doc("slip", [
        ss.step("l1", "load", 300.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0)], k_est=40.0))
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 60_000)
    assert_ended(st, "SLIP")
    assert "BREAK" not in str(getattr(st, "end_reason", "")).upper()
    check_guard_stop(m, t0, "specimen_slip", "SLIP")


@pytest.mark.req("SW-SEQ-007")
def test_break_detected_during_a_travel_step(m4):
    """Spring 20 N/mm from test 2 mm breaks at a deflection of 6 mm (120 N) and keeps 50 % (twin M4 break by
    travel + residual): drop 50 % > 20 % of the running maximum ≥ 1 % FS → BREAK_DETECTED in the TRAVEL step."""
    m = m4
    m.rig.act("specimen", kind="spring", k_n_per_mm=20.0, x_contact_um=m.world(ZERO_MM + 2.0),
              break_travel_um=6000.0, break_residual_pct=50.0)
    seq = m.load(ss.seq_doc("break travel", [
        ss.step("a", "travel", 5.0, speed_mm_s=2.0, settle_s=0.5, capture_s=0.5),
        ss.step("b", "travel", 15.0, speed_mm_s=2.0, settle_s=0.5, capture_s=0.5),
        ss.step("r", "travel", 0.0, speed_mm_s=5.0)]))
    t0 = m.rig.tw.now_us
    ss.start_sequence(m.rig.be, seq)
    st = ss.run_sequence(m.rig, 60_000)
    assert_ended(st, "BREAK_DETECTED")
    check_guard_stop(m, t0)
    assert m.rig.tw.act("query", what="world")["specimen_broken"] is True


# ============================================================================================ 5 pause / resume
def two_step_doc() -> dict:
    return ss.seq_doc("pause resume", [
        ss.step("a", "travel", 5.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0),
        ss.step("b", "travel", 25.0, speed_mm_s=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=10.0)])


@pytest.mark.req("SW-SEQ-007", "SW-STOP-004", "SW-SEQ-004")
@pytest.mark.parametrize("where", ["move", "capture", "button"])
def test_pause_resume_mid_step_reissues_the_step(m4, where):
    """GUI PAUSE during the 2nd step's move (or during the 1st step's capture; or the physical PAUSE button during
    the move, resumed by pressing it again = RESUME_REQUEST): PAUSE / FW pause, VALID 0 while PAUSED, RESUME, the
    interrupted step re-issued with the same absolute target, settle + capture anew, sequence finished."""
    m = m4
    rig, tw = m.rig, m.rig.tw
    m.rig.act("specimen", kind="spring", k_n_per_mm=10.0, x_contact_um=m.world(ZERO_MM + 2.0))
    seq = m.load(two_step_doc())
    t0 = tw.now_us
    ss.start_sequence(rig.be, seq)
    tgt = {"move": ZERO_MM + 25.0, "button": ZERO_MM + 25.0, "capture": ZERO_MM + 5.0}[where]

    def at_pause_point(st) -> bool:  # noqa: ANN001
        if where == "capture":
            return any("VALID" in d["flags"] for d in ss.data(tw, t0)[-8:])
        c = move_cmds(m, t0, tgt)
        return bool(c) and tw.now_us - c[0]["last_us"] > 3_000_000
    ss.run_sequence(rig, 30_000, step_ms=1.0, on_tick=at_pause_point)
    t_p = tw.now_us
    if where == "button":
        rig.act("button", name="pause", pressed=True)
        rig.advance(100)
        rig.act("button", name="pause", pressed=False)
    else:
        rig.be.sequencer.pause()
    wait_for(m, lambda: ss.state_name(rig.be.sequencer.status()) == "PAUSED", 2_000)
    rig.advance(1500)
    assert ss.state_name(rig.be.sequencer.status()) == "PAUSED"
    assert not any("VALID" in d["flags"] for d in ss.data(tw, t_p + 30_000))           # VALID 0 while paused
    assert ss.no_motion_after(tw, t_p) == []
    if where != "button":
        assert len(ss.rx_cmds(tw, t_p, ("PAUSE",))) == 1
    paused = ss.events(tw, t_p, ("PAUSED",))
    assert len(paused) == 1 and paused[0]["arg"] == (2 if where == "button" else 1), paused
    t_r = tw.now_us
    if where == "button":
        rig.act("button", name="pause", pressed=True)
        rig.advance(100)
        rig.act("button", name="pause", pressed=False)
        assert ss.events(tw, t_r, ("RESUME_REQUEST",))
    else:
        g = rig.be.sequencer.resume()
        assert getattr(g, "ok", True), ss.flatten(g)
    st = ss.run_sequence(rig, 60_000)
    assert_finished(st)
    after = [c for c in ss.rx_cmds(tw, t_r) if c["name"] in ("RESUME", "MOVE_ABS")]
    assert after[0]["name"] == "RESUME" and after[1]["name"] == "MOVE_ABS", after[:3]
    assert after[1]["fields"]["target_um"] == round(tgt * 1000)                      # the same absolute target
    runs = ss.valid_runs(ss.data(tw, t0))
    if where == "capture":                         # the open window was discarded by the pause (FW cleared VALID)
        assert ss.events(tw, t_p, ("VALID_CLEARED",)), "FW did not clear VALID at the pause"
        assert len(runs) == 3 and all(len(r) >= 78 for r in (runs[1], runs[2])), [len(r) for r in runs]
    else:
        assert len(runs) == 2 and all(len(r) >= 78 for r in runs), [len(r) for r in runs]


# ============================================================================================ 6 stop / abort
@pytest.mark.req("SW-SEQ-007", "SW-STOP-003")
@pytest.mark.parametrize("control", ["stop", "abort", "global_halt"])
def test_stop_and_abort_controls(m4, control):
    """During the 2nd step's move: sequencer.stop() → STOP mode 1, STOPPED; sequencer.abort() → HALT, ABORTED;
    Backend.halt() (Pause/Break) → HALT, the sequence ends ABORTED (FW-reported HALT terminates it). Afterwards no
    motion command and no SET_VALID 1 from the sequence."""
    m = m4
    rig, tw = m.rig, m.rig.tw
    seq = m.load(two_step_doc())
    t0 = tw.now_us
    ss.start_sequence(rig.be, seq)
    ss.run_sequence(rig, 30_000, on_tick=lambda st: bool(move_cmds(m, t0, ZERO_MM + 25.0))
                    and tw.now_us - move_cmds(m, t0, ZERO_MM + 25.0)[0]["last_us"] > 2_000_000)
    t_c = tw.now_us
    {"stop": rig.be.sequencer.stop, "abort": rig.be.sequencer.abort, "global_halt": rig.be.halt}[control]()
    st = ss.run_sequence(rig, 10_000)
    if control == "stop":
        stops = ss.rx_cmds(tw, t_c, ("STOP",))
        assert [s["fields"]["mode"] for s in stops][:1] == [1], stops
        assert_ended(st, "STOP")
        assert ss.state_name(st) != "ABORTED"
    else:
        assert ss.rx_cmds(tw, t_c, ("HALT",)), "no HALT on the wire"
        assert_ended(st, "ABORT")
        assert ss.events(tw, t_c, ("HALT_SET",))
    rig.advance(2000)
    assert ss.no_motion_after(tw, t_c) == []
    assert not any("VALID" in d["flags"] for d in ss.data(tw, t_c))


# ============================================================================================ 7 ALM
@pytest.mark.req("SW-SEQ-007")
def test_alm_during_a_sequence_controlled_stop_driver_alarm(m4):
    """ALM becomes active during the 2nd step's move: the SW sends a controlled STOP (mode 1), the sequence ends
    with reason "driver alarm" and is not retried (SRS SW-SEQ-007, D-33 c)."""
    m = m4
    rig, tw = m.rig, m.rig.tw
    seq = m.load(two_step_doc())
    t0 = tw.now_us
    ss.start_sequence(rig.be, seq)
    ss.run_sequence(rig, 30_000, on_tick=lambda st: bool(move_cmds(m, t0, ZERO_MM + 25.0))
                    and tw.now_us - move_cmds(m, t0, ZERO_MM + 25.0)[0]["last_us"] > 2_000_000)
    t_a = tw.now_us
    rig.act("alm", active=True)
    st = ss.run_sequence(rig, 10_000)
    stops = ss.rx_cmds(tw, t_a, ("STOP",))
    assert stops and stops[0]["fields"]["mode"] == 1, stops
    assert stops[0]["first_us"] - t_a <= 100_000, stops[0]                 # next DATA / poll + one frame
    assert_ended(st, "ALARM")
    rig.advance(3000)
    assert ss.no_motion_after(tw, t_a) == []                               # no retry


# ============================================================================================ 8 link loss
@pytest.mark.req("SW-STOP-003", "SAF-SW-003", "SW-SEQ-004")
@pytest.mark.parametrize("where", ["move", "capture"])
def test_link_loss_during_a_sequence(m4, where):
    """PC → FW bytes lost for 3 s (``inject link_silence``; FW → PC keeps running). The sequence ends **ABORTED**
    (SW_design §10.3 termination: link loss → terminate_all → ABORTED; B6-10 end reason LINK_LOST passed through)
    and is never resumed: after the link returns no motion command / SET_VALID 1 reaches the FW.
    - during a move: the FW link watchdog (ICD §9.1) stops the axis (STOPPED cause LINK_WDG) and clears VALID;
    - during a capture (axis idle): since ICD v0.7.4 (D-47 a) the FW watchdog clears VALID in every motion state
      (EVENT VALID_CLEARED, cause LINK_WDG, no stop): no VALID frame later than timeout + one frame after the
      silence started, and the SW never re-asserts it (SW-SEQ-004)."""
    m = m4
    rig, tw = m.rig, m.rig.tw
    seq = m.load(two_step_doc())
    t0 = tw.now_us

    def point(st) -> bool:  # noqa: ANN001
        if where == "capture":
            return any("VALID" in d["flags"] for d in ss.data(tw, t0)[-8:])
        c = move_cmds(m, t0, ZERO_MM + 25.0)
        return bool(c) and tw.now_us - c[0]["last_us"] > 2_000_000
    ss.start_sequence(rig.be, seq)
    ss.run_sequence(rig, 30_000, step_ms=1.0, on_tick=point)
    t_l = tw.now_us
    rig.act("inject", fault="link_silence", duration_ms=3000)
    st = ss.run_sequence(rig, 15_000)
    rig.advance(5000)                                                     # link back (silence over)
    t_back = t_l + 3_000_000
    if where == "move":
        wdg = ss.events(tw, t_l, ("LINK_WDG",))
        assert wdg, "FW link watchdog did not trip during the move"
        stp = ss.events(tw, t_l, ("STOPPED",))
        assert stp and stp[0]["arg"] == 13, stp                           # cause LINK_WDG
        assert not any("VALID" in d["flags"] for d in ss.data(tw, t_l))
    else:
        clr = ss.events(tw, t_l, ("VALID_CLEARED",))
        assert clr and clr[0]["arg"] == 13, clr                           # D-47 a: cause LINK_WDG, idle
        assert ss.events(tw, t_l, ("STOPPED", "LINK_WDG")) == []          # not moving: no stop, no LINK_WDG
        late = [d for d in ss.data(tw, t_l + 1_000_000 + FRAME + SLACK) if "VALID" in d["flags"]]
        assert late == [], "VALID still 1 after the link-watchdog timeout (D-47 a / SW-SEQ-004)"
    assert ss.no_motion_after(tw, t_back) == []
    assert ss.state_name(st) == "ABORTED", f"link loss must abort the sequence: {ss.outcome_text(st)}"


# ============================================================================================ 9 report
RESULT_NUMS = ("n", "n_expected", "f_mean_n", "f_std_n", "f_min_n", "f_max_n", "f_se_n", "f_drift_n", "x_mean_mm",
               "x_std_mm", "x_drift_mm", "raw_mean", "raw_std", "raw_min", "raw_max", "raw_se", "raw_drift",
               "t_reached_s", "k_est_n_mm", "f_end_n", "x_end_mm")


def result_numbers(r) -> dict:  # noqa: ANN001
    """The numeric StepResult fields (SW_design §15.5f B6-08); NaN mapped to None so that equal NaNs compare."""
    out = {}
    for k in RESULT_NUMS:
        v = r.get(k) if isinstance(r, dict) else getattr(r, k, None)
        out[k] = None if v is None or (isinstance(v, float) and math.isnan(v)) else v
    out["flags"] = tuple(sorted(r.get("flags", ()) if isinstance(r, dict) else getattr(r, "flags", ())))
    out["uid"] = r.get("uid") if isinstance(r, dict) else getattr(r, "uid", None)
    return out


def report_results(rig, folder: Path) -> list[dict]:  # noqa: ANN001
    try:
        res = rig.be.reports.load_result(str(folder))
    except NotImplementedError as e:
        pytest.xfail(f"Implementer B's reports.load_result not delivered yet ({e})")
    return [result_numbers(r) for r in res.results]


@pytest.mark.req("SW-REP-001", "SW-REP-002", "SW-REP-003")
def test_report_offline_equals_online_and_the_wire(m4):
    """Travel 5 / 10 mm + load 150 N (spring 20 N/mm from test 2 mm: the load step starts at 160 N, so its approach
    runs toward the soft minimum, cmp LE) + return:
    - report.json / report.html / data.csv / meta.json exist after the sequence (SW-REP-001);
    - the online step results (``sequencer.results()``, the live window statistics) equal the report built at the
      end and the report rebuilt offline from the recording (``reports.build_async`` → ``load_result``) field by
      field (SW-REP-003);
    - Integrator oracle: each TRAVEL window's N and raw mean equal the recomputation from the DATA frames on the
      wire (ICD §7.6 rule over [t_reached + settle, + capture], t_reached = MOVE_DONE device time) (SW-REP-002);
    - an offline rebuild with tare + 1000 counts shifts every F by −K·1000 and leaves raw unchanged (SW-REP-003)."""
    m = m4
    rig, tw = m.rig, m.rig.tw
    rig.act("specimen", kind="spring", k_n_per_mm=20.0, x_contact_um=m.world(ZERO_MM + 2.0))
    seq = m.load(ss.seq_doc("report", [
        ss.step("a", "travel", 5.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0),
        ss.step("b", "travel", 10.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0),
        ss.step("l", "load", 150.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=10.0)], k_est=20.0))
    t0 = tw.now_us
    ss.start_sequence(rig.be, seq)
    st = ss.run_sequence(rig, 90_000)
    assert_finished(st)
    online = [result_numbers(r) for r in rig.be.sequencer.results()]
    rig.advance(3000)                                     # 1 s recording tail + report build at the end
    folder = ss.recording_folder(m.rec)
    for f in ("data.csv", "meta.json", "report.json", "report.html"):
        assert (folder / f).is_file(), f"{f} missing in {folder}"
    assert "<svg" in (folder / "report.html").read_text(encoding="utf-8")         # self-contained figures
    at_end = report_results(rig, folder)
    with_window = [r for r in online if r["n"]]
    assert [r["uid"] for r in with_window] == ["a", "b", "l"], online
    assert at_end == online, "report built at the sequence end != live step results"
    rig.result(rig.be.reports.build_async(str(folder)), 30_000)
    assert report_results(rig, folder) == online, "offline rebuild != live step results"
    # Integrator oracle (wire)
    frames = ss.data(tw, t0)
    done = [e for e in ss.events(tw, t0, ("MOVE_DONE",)) if e["arg"] == 0]
    for k, r in enumerate(with_window[:2]):
        t_r = done[k]["t_us"]
        sel = ss.steady_frames(frames, t_r + 0.5e6, t_r + 1.5e6)
        assert r["n"] == len(sel) >= 0.8 * 1.0 * 80, (k, r["n"], len(sel))
        assert r["raw_mean"] == pytest.approx(ss.mean([d["afe_raw"] for d in sel]), rel=1e-12, abs=1e-6)
        assert "INCOMPLETE" not in r["flags"]
    load = with_window[2]
    assert abs(load["f_mean_n"] - 150.0) <= 2.0 and "ON_TARGET" in load["flags"], load
    # re-applied tare (offline): F shifts by −K·1000, raw unchanged
    rig.result(rig.be.reports.build_async(str(folder), tare=m.tare_raw + 1000.0), 30_000)
    shifted = report_results(rig, folder)
    for a, b in zip([r for r in shifted if r["n"]], with_window):
        assert a["raw_mean"] == b["raw_mean"]
        assert a["f_mean_n"] == pytest.approx(b["f_mean_n"] - m.K * 1000.0, abs=1e-6)
    # B6-08: t_reached_s = the device time the step reached its target (TRAVEL: MOVE_DONE t_us)
    assert [r["t_reached_s"] for r in with_window[:2]] == pytest.approx([e["t_us"] / 1e6 for e in done[:2]], abs=1e-6)
