"""SIM-vs-twin differential for a **short sequence** (M4, D-46; continues MC2-6 / test_sim_vs_twin_motion.py).

Part A — board level (runs now): the FW side of a sequence — park at a world-determined position, a VALID capture
window, a MOVE_UNTIL_LOAD approach on a spring, a trim to an absolute target, a capture interrupted by PAUSE
(FW clears VALID) and resumed (RESUME, the step re-issued = MOVE_ABS to the same target: no pulse), a re-capture,
a capture ended by HALT, the return — driven identically against B's simulator and A's firmware in the twin
(``lockstep_boards``: one PC-side ``Link`` on ``ref_codec``, lock-step virtual time, no wall clock). Compared per the
tools/README "SIM-vs-twin comparison rule": responses, EVENT codes/args, latches and world-determined positions
exactly; positions after a load-threshold stop within ``ceil(v · 12.5 ms) + 1`` steps (HX711 conversion phase);
VALID windows by frame count ±1 (conversion phase) and by their raw values exactly (noise 0, same position).

Part B — backend level (xfail until B's sequencer lands): Implementer B's ``Backend`` with the sequencer runs the
same short sequence (TRAVEL capture, LOAD 100 N on k = 50 N/mm with k_est 40, return) once against the simulator
(lock-step ``sim`` endpoint) and once against the twin (``twin_rig.Rig``): the same outcome, the same PC command
structure (TRAVEL targets and MOVE_UNTIL_LOAD fields exact, raw_stop ±2 counts, trims ±1), the same number of VALID
windows (lengths ±1 frame), TRAVEL window raw equal ±1 count, LOAD window force within tol on both.

Verifies: SYS-008, IF-010 (behavioural equality), SW-SEQ-004, SW-SEQ-006, FW-MOT-006, FW-STR-003, SAF-FW-001,
          SAF-FW-023, D-30, D-31
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

import ref_codec as rc
import seq_support as ss
from test_sim_vs_twin_motion import Script, enable_home as board_enable_home, pos_tol, world_setup

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.io.sim.board", "SimBoard")]

CPN = 3285.0
OFFSET = 50_000
SPRING = {"kind": "spring", "k_n_per_mm": 50.0, "x_contact_um": 15_000}        # world µm (both boards)


# ==================================================================================== Part A: board level
class SeqScript(Script):
    """Script + DATA bookkeeping: VALID windows are summarised as (frame count, sorted set of raw values)."""

    def mark(self) -> int:
        self.L.poll()
        return len(self.L.frames)

    def data_since(self, i: int) -> list[dict]:
        self.L.poll()
        return [rc.decode_data(f.payload) for f in self.L.frames[i:] if f.type == rc.ASYNC["DATA"]]

    def window(self, label: str, ms: float, *, end: str = "SET_VALID") -> None:
        """SET_VALID 1 → ``ms`` of capture → SET_VALID 0 (or the given ending command): records the responses
        exactly, the VALID frame count with ±1 (conversion phase) and the set of raw values exactly."""
        self.resp(label + ".on", "SET_VALID", {"valid": 1})
        i = self.mark()
        self.run(ms)
        if end == "SET_VALID":
            self.resp(label + ".off", "SET_VALID", {"valid": 0})
        else:
            self.resp(label + "." + end.lower(), end)
        self.run(50)
        frames = self.data_since(i)
        valid = [d for d in frames if "VALID" in d["flags"]]
        self.approx[label + ".valid_frames"] = (len(valid), 1)
        self.rec(label + ".raw_set", sorted({d["afe_raw"] for d in valid}))
        self.rec(label + ".moving_in_window", any("MOVING" in d["flags"] for d in valid))
        self.rec(label + ".valid_after", any("VALID" in d["flags"] for d in frames[len(frames) - 3:]))


def sc_short_sequence(s: SeqScript) -> None:
    v_load = 2_000
    s.resp("seq.stream", "STREAM_START")
    s.b.act("specimen", **SPRING)
    # TRAVEL step to 20 mm (machine): world-determined → exact; capture 1 s
    s.resp("seq.travel", "MOVE_ABS", {"target_um": 20_000, "v_um_s": 10_000, "a_um_s2": 0})
    s.wait_state()
    s.snap("seq.travel.done")
    s.run(500)                                                            # settle
    s.window("seq.cap1", 1000)
    # LOAD step: approach (MOVE_UNTIL_LOAD to 300 N − band) → threshold stop (conversion phase: toleranced)
    raw_stop = OFFSET + math.floor(CPN * 294.8)
    s.resp("seq.mul", "MOVE_UNTIL_LOAD", {"bound_um": 290_000, "v_um_s": v_load, "a_um_s2": 0, "raw_stop": raw_stop,
                                         "cmp": 0})
    s.wait_state()
    s.run(50)          # the stop instant is conversion-phase dependent: PEND (driver model, lag 5 ms) settled on both
    s.snap("seq.mul.done", tol=pos_tol(v_load, 12.5))
    # trim to an absolute (world-determined) target at 0.2 mm/s → exact again
    s.resp("seq.trim", "MOVE_ABS", {"target_um": 21_900, "v_um_s": 200, "a_um_s2": 0})
    s.wait_state()
    s.snap("seq.trim.done")
    s.run(100)
    # capture interrupted by PAUSE: the FW clears VALID (VALID_CLEARED cause PC_PAUSE), PAUSED latched
    s.window("seq.cap2", 300, end="PAUSE")
    s.snap("seq.paused")
    s.resp("seq.paused.move_refused", "MOVE_ABS", {"target_um": 21_900, "v_um_s": 200, "a_um_s2": 0})
    s.resp("seq.resume", "RESUME")
    p0 = s.b.pulses()
    s.resp("seq.reissue", "MOVE_ABS", {"target_um": 21_900, "v_um_s": 200, "a_um_s2": 0})   # same target: no pulse
    s.wait_state()
    s.rec("seq.reissue.pulses", s.b.pulses() - p0)
    s.snap("seq.reissue.done")
    s.window("seq.cap3", 1000)
    # capture ended by HALT (abort): VALID cleared by the FW (SET_VALID itself stays accepted in every state,
    # ICD §4.3 table — the SW must not re-assert it, SW-SEQ-004)
    s.window("seq.cap4", 200, end="HALT")
    s.snap("seq.halted")
    s.resp("seq.halted.set_valid", "SET_VALID", {"valid": 1})
    s.resp("seq.halted.set_valid0", "SET_VALID", {"valid": 0})
    s.resp("seq.halt_clear", "HALT_CLEAR")
    s.resp("seq.return", "MOVE_ABS", {"target_um": 20_000, "v_um_s": 10_000, "a_um_s2": 0})
    s.wait_state()
    s.snap("seq.return.done")
    s.rec("seq.world_x_um", round(s.b.world_x_um(), 3))


def _run(board) -> SeqScript:  # noqa: ANN001
    s = SeqScript(board)
    board.advance_ms(5)
    world_setup(s)
    board_enable_home(s)
    sc_short_sequence(s)
    return s


@pytest.fixture(scope="module")
def seq_runs(twin_exe, tmp_path_factory):
    from bend_stand.core import protocol_gen as pg

    from lockstep_boards import Link, SimBoardSide, TwinBoard

    tb = TwinBoard(twin_exe, tmp_path_factory.mktemp("twin_m4"))
    try:
        tb.advance_ms(5)
        feats = Link(tb).cmd("GET_INFO")["info"]["features"]
        mask = sum(int(pg.Features[n]) for n in feats if n != "TWIN")
        if not {"MOTION", "HOMING", "MOVE_UNTIL_LOAD"} <= set(feats):
            pytest.skip("A's FW build reports no MOTION/HOMING/MOVE_UNTIL_LOAD")
        tw = _run(tb)
    finally:
        tb.close()
    sb = SimBoardSide(mask, tmp_path_factory.mktemp("sim_m4"))
    try:
        sim = _run(sb)
    finally:
        sb.close()
    return tw, sim


SECTIONS = ("seq.stream", "seq.travel", "seq.cap1", "seq.mul", "seq.trim", "seq.cap2", "seq.paused", "seq.resume",
            "seq.reissue", "seq.cap3", "seq.cap4", "seq.halted", "seq.halt_clear", "seq.return", "seq.world_x_um")


def _items(s: Script, prefix: str) -> list[tuple]:
    return [x for x in s.exact if x[0] == prefix or x[0].startswith(prefix + ".")]


@pytest.mark.req("SYS-008", "IF-010", "SW-SEQ-004", "FW-MOT-006", "FW-STR-003", "SAF-FW-001", "SAF-FW-023")
@pytest.mark.parametrize("section", SECTIONS)
def test_sim_vs_twin_short_sequence(seq_runs, section):
    tw, sim = seq_runs
    a, b = _items(tw, section), _items(sim, section)
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    if len(a) != len(b):
        diffs.append((f"twin {len(a)} items", f"sim {len(b)} items"))
    pos = [(k, v, sim.approx[k][0], tol) for k, (v, tol) in tw.approx.items()
           if (k == section or k.startswith(section + ".")) and k in sim.approx and abs(v - sim.approx[k][0]) > tol]
    lines = [f"  twin {x}\n  sim  {y}" for x, y in diffs] + [f"  {k}: twin {v} sim {w} (tol ±{t})" for k, v, w, t in pos]
    assert not diffs and not pos, f"SIM vs twin [{section}]:\n" + "\n".join(lines)


def test_short_sequence_board_expectations(seq_runs):
    """The twin run itself (absolute expectations, so that an equal-but-wrong pair cannot pass): VALID windows
    have 80 ± 1 frames at 80 SPS and no MOVING frame; the PAUSE and HALT clear VALID (no VALID frame at the end of
    those windows); the re-issue after RESUME moves no pulse; a motion start is refused while PAUSED."""
    tw, _ = seq_runs
    ex = dict(tw.exact)
    for w in ("seq.cap1", "seq.cap3"):
        assert abs(tw.approx[w + ".valid_frames"][0] - 80) <= 1
        assert ex[w + ".moving_in_window"] is False and ex[w + ".valid_after"] is False
    for w in ("seq.cap2", "seq.cap4"):
        assert ex[w + ".valid_after"] is False and tw.approx[w + ".valid_frames"][0] > 0
    assert ex["seq.reissue.pulses"] == 0
    assert ex["seq.halted.set_valid"][0] == "OK"                        # accepted in every state (ICD §4.3)
    assert ex["seq.paused.move_refused"][0] == "E_STATE"
    paused_ev = [e for e in ex["seq.paused.events"] if e[0] in ("PAUSED", "VALID_CLEARED")]
    assert ("PAUSED", 1) in [e[:2] for e in paused_ev]
    assert ("VALID_CLEARED", rc.STOP_CAUSE["PC_PAUSE"]) in [e[:2] for e in paused_ev]


# ==================================================================================== Part B: backend level
class SimRig:
    """B's Backend on the lock-step in-process simulator, with the subset of ``twin_rig.Rig`` the M3/M4 helpers use."""

    def __init__(self, tmp: Path) -> None:
        from bend_stand.core.backend import Backend, BackendSettings

        self.be = Backend(BackendSettings(clock="lockstep", test_hooks=True, wire_log=True, hotkey="off",
                                          recordings_root=str(tmp / "rec")))
        self.hooks = self.be.test_hooks
        self.be.start()
        self.hooks.result(self.be.connect_async("sim"), 5000)
        self.hooks.advance(50)

    def act(self, action: str, **kw) -> dict:  # noqa: ANN003
        r = self.be.sim.act(action, **kw)
        assert r is None or r.get("ok", True), (action, kw, r)
        return r or {}

    def advance(self, ms: float, step_ms: float = 1.0) -> None:
        self.hooks.advance(ms, step_ms)

    def run_until(self, pred, timeout_ms: float = 5000.0, step_ms: float = 1.0) -> bool:  # noqa: ANN001
        return self.hooks.run_until(pred, timeout_ms, step_ms)

    def result(self, fut, timeout_ms: float = 5000.0):  # noqa: ANN001, ANN201
        return self.hooks.result(fut, timeout_ms)

    @property
    def now_ms(self) -> float:
        return self.be.clock.monotonic_ns() / 1e6

    def world_x_um(self) -> float:
        return float(self.be.sim.act("query", what="world")["x_um_true"])

    def close(self) -> None:
        self.be.shutdown()


def _backend_flow(rig, tmp: Path, world_x) -> dict:  # noqa: ANN001
    """Calibrate (0 + 1 + 10 kg), tare, home, park at 20 mm = test zero, spring 50 N/mm from test 5 mm, run the
    short sequence; returns the PC command structure and the VALID windows from B's own wire log."""
    from test_backend_twin_m3 import enable_home, load_calibration, tare

    if reason := ss.sequencer_stub(rig.be):
        pytest.xfail(reason)
    rig.act("load_offset", counts=OFFSET)
    rig.act("afe", noise_counts=0.0, rate_error=0.0)
    rig.advance(500)
    load_calibration(rig)
    tare(rig, 2.0)
    rig.result(rig.be.limits.recheck_async())
    enable_home(rig)
    rig.result(rig.be.motion.move_to(20.0, speed_mm_s=10.0), 20_000)
    rig.advance(200)
    rig.be.motion.set_test_zero()
    x0 = world_x() - rig.be.status().motion.position_mm * 1000.0
    rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=x0 + 25_000.0)
    doc = ss.seq_doc("short", [
        ss.step("t", "travel", 3.0, speed_mm_s=5.0, settle_s=0.5, capture_s=1.0),
        ss.step("l", "load", 100.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.5, capture_s=1.0),
        ss.step("r", "travel", 0.0, speed_mm_s=10.0)], k_est=40.0)
    seq = ss.load_sequence(rig.be, doc, tmp / "seq")
    n0 = len(rig.be.test_hooks.wire_log())
    ss.start_sequence(rig.be, seq)
    st = ss.run_sequence(rig, 90_000)
    K = float(rig.be.calibrations.active_load()["fit"]["k_n_per_count"])
    tare_raw = float(rig.be.status().tare.tare_raw)
    cmds, frames = [], []
    for r in rig.be.test_hooks.wire_log()[n0:]:
        f = bytes(r.frame)
        ftype, n = f[2], int.from_bytes(f[4:6], "little")
        pl = f[6:6 + n]
        if r.direction == "TX" and rc.CMD_NAME.get(ftype) in ("MOVE_ABS", "MOVE_UNTIL_LOAD", "SET_VALID", "STOP",
                                                            "HALT", "PAUSE", "RESUME", "HOME", "JOG"):
            name = rc.CMD_NAME[ftype]
            cmds.append((name, rc.decode_request(name, pl)))
        elif r.direction == "RX" and ftype == rc.ASYNC["DATA"]:
            frames.append(rc.decode_data(pl))
    runs = ss.valid_runs(frames)
    return {"state": ss.state_name(st), "outcome": ss.outcome_text(st), "cmds": cmds, "runs": runs, "K": K,
            "tare_raw": tare_raw}


@pytest.fixture(scope="module")
def backend_runs(twin_exe, tmp_path_factory):
    import os

    from twin_rig import Rig

    out = {}
    tmp = tmp_path_factory.mktemp("be_twin")
    old = {k: os.environ.get(k) for k in ("APPDATA", "LOCALAPPDATA")}
    try:
        for name in ("twin", "sim"):
            tmp = tmp_path_factory.mktemp(f"be_{name}")
            os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = str(tmp / "appdata"), str(tmp / "local")
            if name == "twin":
                rig = Rig(twin_exe, tmp / "twin", recordings=tmp / "rec")
                rig.connect()
                world = lambda r=rig: float(r.tw.act("query", what="world")["x_um_true"])  # noqa: E731
            else:
                rig = SimRig(tmp)
                world = rig.world_x_um
            try:
                out[name] = _backend_flow(rig, tmp, world)
            finally:
                rig.close()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


@pytest.mark.needs_b("bend_stand.core.sequencer")
@pytest.mark.req("SYS-008", "SW-SEQ-003", "SW-SEQ-004", "SW-SEQ-006")
def test_backend_sequence_sim_vs_twin(backend_runs):
    tw, sim = backend_runs["twin"], backend_runs["sim"]
    assert tw["state"] == sim["state"], (tw["outcome"], sim["outcome"])
    assert "FINISH" in tw["outcome"] or tw["state"] in ("FINISHED", "DONE"), tw["outcome"]

    def structure(c: list) -> list:
        out, trims = [], 0
        for name, f in c:
            if name == "MOVE_ABS" and f["v_um_s"] == 200:
                trims += 1
                continue
            if trims:
                out.append(("TRIMS", trims))
                trims = 0
            out.append((name, f))
        return out

    a, b = structure(tw["cmds"]), structure(sim["cmds"])
    assert [x[0] for x in a] == [x[0] for x in b], (a, b)
    for (name, fa), (_, fb) in zip(a, b):
        if name == "TRIMS":
            assert abs(fa - fb) <= 1 and max(fa, fb) <= 10, (fa, fb)
        elif name == "MOVE_UNTIL_LOAD":
            assert {k: v for k, v in fa.items() if k != "raw_stop"} == {k: v for k, v in fb.items() if k != "raw_stop"}
            assert abs(fa["raw_stop"] - fb["raw_stop"]) <= 2, (fa, fb)
        else:
            assert fa == fb, (name, fa, fb)
    assert len(tw["runs"]) == len(sim["runs"]) == 2, (len(tw["runs"]), len(sim["runs"]))
    for ra, rb in zip(tw["runs"], sim["runs"]):
        assert abs(len(ra) - len(rb)) <= 1
    t_a = ss.mean([d["afe_raw"] for d in tw["runs"][0]])
    t_b = ss.mean([d["afe_raw"] for d in sim["runs"][0]])
    assert abs(t_a - t_b) <= 1.0, (t_a, t_b)
    for r in (tw, sim):
        f = ss.mean([r["K"] * (d["afe_raw"] - r["tare_raw"]) for d in r["runs"][1]])
        assert abs(f - 100.0) <= 2.0, f
