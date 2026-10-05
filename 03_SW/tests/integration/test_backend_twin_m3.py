"""M3 integration flows: Implementer B's backend (engines, SW load limits, recorder) ⇄ A's firmware in the twin,
both in one lock-step virtual time (``twin_rig.Rig``), with the twin's M3 load model (ICD v0.7.2 vocabulary:
``weight`` = known masses on the cell, ``afe drift_counts_per_s`` / ``creep_pct`` / ``nonlin_pct_fs``,
``specimen ... relax_pct``; R2 §3 cell figures: 3 285 counts/N, noise 45 counts rms at 80 SPS).

Flows (SW_design §8, §9, §6.1–§6.2; SRS SW-TARE, SW-CAL, SAF-SW-001, SW-ACQ):
- tare: accepted at rest (tare_raw = cell offset within the noise of the mean); refused with zero drift beyond
  max(2·std, 20) counts over the window; refused while the AFE is stale;
- load calibration zero + 1 kg + 10 kg: K = 1/3285 N/count within 0.2 %, LOW_SPAN (98 N < 20 % FS), accepted;
- travel calibration 10 mm + 50 mm with the measured distances injected from the twin world (true mechanics
  790 steps/mm while the board runs 800): steps/mm accepted ≈ 790, saved to NVM;
- PC load-limit STOP latency (SAF-SW-001, calibrated path): spring specimen pushed at 2 mm/s, SW pull limit
  50 N: STOP on the wire ≤ 50 ms (virtual) after the end of the first violating DATA frame, FW STOPPED (PC);
- recording of a moving test: one data.csv row per DATA frame on the wire during the recording, positions and
  MOVING as sent, sidecar complete (runs now — B's M1/M2 recorder).

A flow whose engine is still B's M3 stub (gate item NOT_IMPLEMENTED, SW_design §15) is reported **xfail** with
that reason at run time, so each test activates by itself when B's engine lands; a difference found then is an
Integrator finding against this file or B's code, never a silent change of the expectation.

Verifies: SW-TARE-001, SW-TARE-002, SW-TARE-003, SW-CAL-001, SW-CAL-002, SW-CAL-003, SW-CAL-004, SW-CAL-005,
          SW-CAL-006, SW-CAL-007, SW-CAL-008, SAF-SW-001, SAF-SW-002, SW-ACQ-001, SW-ACQ-002, SYS-008
"""
from __future__ import annotations

import csv
import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import pytest

import ref_codec as rc

pytestmark = [pytest.mark.twin, pytest.mark.needs_b("bend_stand.core.backend", "Backend")]

CPN = 3285.0                     # twin world counts per newton (tools/README scenario default, R2 §3)
G0 = 9.80665
OFFSET = 50_000
NOISE = 45.0                     # counts rms at 80 SPS (R2 §3)
FS_N = 200 * G0
MS = 1_000_000


# ============================================================================================ helpers
@pytest.fixture
def rig(twin_exe, tmp_path, monkeypatch):
    from twin_rig import Rig

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))          # calibration store / session (SW §9.6)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    r = Rig(twin_exe, tmp_path / "twin", recordings=tmp_path / "rec")
    r.act("load_offset", counts=OFFSET)
    r.act("afe", noise_counts=NOISE, rate_error=0.0)
    r.connect()
    r.advance(500)
    yield r
    r.close()


def data_frames(rig, since_us: float = 0.0) -> list[tuple[dict, dict]]:  # noqa: ANN001
    """(wire_log entry, decoded DATA payload) of every DATA frame the FW put on the wire since `since_us`."""
    out = []
    for w in rig.tw.wire_log:
        if w["dir"] == "tx" and w["type"] == rc.ASYNC["DATA"] and w["first_us"] >= since_us:
            b = bytes.fromhex(w["hex"])
            out.append((w, rc.decode_data(b[6:6 + int.from_bytes(b[4:6], "little")])))
    return out


def world_x0(rig) -> float:  # noqa: ANN001
    """World x (µm) of machine position 0 (after HOME; OI-B-M2-04 convention)."""
    w = rig.tw.act("query", what="world")
    return w["x_um_true"] - w["pos_steps"] * 1000.0 / 800.0


def require(rig, gate: str, what: str) -> None:  # noqa: ANN001
    """xfail (run time) while B's M3 part behind `gate` is still the stub (gate item NOT_IMPLEMENTED)."""
    from bend_stand.core.model import GateId

    g = rig.be.status().gates.get(GateId(gate))
    items = getattr(g, "items", ())
    stub = [i for i in items if "NOT_IMPLEMENTED" in str(i.code)]
    if stub:
        pytest.xfail(f"Implementer B's {what} not delivered yet (gate {gate}: {stub[0].text})")


def stub_xfail(g, what: str) -> None:  # noqa: ANN001
    """The action itself still answers B's M3 stub (NOT_IMPLEMENTED) although its gate is open: xfail."""
    stub = [i for i in getattr(g, "items", ()) if "NOT_IMPLEMENTED" in str(i.code)]
    if stub:
        pytest.xfail(f"Implementer B's {what} not wired yet ({stub[0].text})")


def free_motion(rig) -> None:  # noqa: ANN001
    """Motion without a load calibration: no-specimen mode (SW-LIM-004, §6.7; SW load limits off, FW limit stays).
    B's transitional tree (PC load limits already need a valid input, no-specimen mode still the stub) → xfail."""
    from bend_stand.core.model import GateId

    g = rig.be.limits.set_no_specimen_mode(True, confirmed=True)
    if g.ok:
        return
    stub = [i for i in g.items if "NOT_IMPLEMENTED" in str(i.code)]
    assert stub, g
    mv = rig.be.status().gates.get(GateId("move"))
    if any("valid input" in i.text for i in getattr(mv, "items", ())):
        pytest.xfail("Implementer B: PC load limits need a valid load input but no-specimen mode is not delivered "
                     f"yet ({stub[0].text})")


def field(obj: Any, *names: str, default: Any = None) -> Any:
    """First attribute / mapping key among `names` (B's result layouts are not frozen until delivery)."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if obj is not None and hasattr(obj, n):
            return getattr(obj, n)
    return default


def flatten(obj: Any) -> str:
    try:
        return json.dumps(dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj, default=str)
    except TypeError:
        return str(obj)


def drive(rig, engine, handlers: dict, timeout_ms: float = 120_000, step_ms: float = 20.0):  # noqa: ANN001, ANN201
    """Run an engine (SW_design §9.1) to DONE: at each phase with ``can_continue`` the handler for that phase
    (world action + operator inputs) runs once, then ``continue_(inputs, confirmed=<confirmation requested>)``."""
    seen: set[tuple[str, int]] = set()
    t_end = rig.now_ms + timeout_ms
    while rig.now_ms < t_end:
        st = engine.state()
        if st.phase == "DONE":
            return st
        if st.phase in ("ABORTED", "REFUSED", "FAILED", "CANCELLED"):
            raise AssertionError(f"{st.kind}: {st.phase} {st.abort_reason} errors={st.errors} warnings={st.warnings}")
        key = (st.phase, st.step_index)
        if st.can_continue and key not in seen:
            seen.add(key)
            h = handlers.get(st.phase)
            inputs = h(st) if h else None
            engine.continue_(inputs, confirmed=st.needs_confirmation is not None)
        elif st.can_continue and st.needs_confirmation is not None:
            engine.continue_(None, confirmed=True)
        rig.advance(step_ms, 1.0)
    raise AssertionError(f"engine not DONE after {timeout_ms} ms (phase {engine.state().phase})")


def fill(st, value: float, *words: str) -> dict:  # noqa: ANN001
    """Operator inputs: `value` into every declared input whose key / label / unit contains one of `words`."""
    out = {}
    for spec in st.inputs:
        text = f"{spec.key} {spec.label} {spec.unit}".lower()
        if not words or any(w in text for w in words):
            out[spec.key] = value
    return out


def enable_home(rig) -> None:  # noqa: ANN001
    be = rig.be
    assert be.motion.enable().ok
    rig.advance(700)
    out = rig.result(be.motion.home(load_confirmed=True), 30_000)
    assert "DONE" in str(out), out
    rig.advance(300)


def tare(rig, window_s: float = 2.0):  # noqa: ANN001, ANN201
    g = rig.be.tare(window_s)
    stub_xfail(g, "Backend.tare")
    assert g.ok, g
    return drive(rig, rig.be.tare_engine, {}, timeout_ms=window_s * 1000 + 10_000)


def load_calibration(rig, masses=(0.0, 1.0, 10.0)):  # noqa: ANN001, ANN201
    lc = rig.be.load_cal
    g = lc.start()
    stub_xfail(g, "load_cal.start")
    assert g.ok, g

    def await_operator(st):  # noqa: ANN001, ANN202
        m = masses[min(st.step_index, len(masses) - 1)]
        rig.act("weight", kg=m)                          # the operator hangs (or removes) the weights
        return fill(st, m, "mass", "kg") or None

    st = drive(rig, lc, {"AWAIT_OPERATOR": await_operator}, timeout_ms=180_000)
    rig.act("weight", kg=0)
    return st


# ============================================================================================ tare
@pytest.mark.req("SW-TARE-001", "SW-TARE-002", "SW-TARE-003")
def test_tare_accepted_at_rest(rig):
    require(rig, "tare", "TareEngine (SW_design §9.5)")
    st = tare(rig, 2.0)
    t = rig.be.status().tare
    raw = field(t, "tare_raw", "raw")
    assert raw is not None, flatten(t)
    assert abs(raw - OFFSET) <= 4 * NOISE / math.sqrt(150), (raw, flatten(st))   # mean of ~160 samples


@pytest.mark.req("SW-TARE-002")
def test_tare_refused_with_zero_drift(rig):
    """Zero drift 60 counts/s → 120 counts over a 2 s window > max(2·std, 20) (SW_design §9.5) → refused."""
    require(rig, "tare", "TareEngine (SW_design §9.5)")
    rig.act("afe", drift_counts_per_s=60.0)
    g = rig.be.tare(2.0)
    stub_xfail(g, "Backend.tare")
    assert g.ok, g
    with pytest.raises(AssertionError, match="REFUSED|drift"):
        drive(rig, rig.be.tare_engine, {}, timeout_ms=12_000)
    assert field(rig.be.status().tare, "tare_raw", "raw") is None


@pytest.mark.req("SW-TARE-002")
def test_tare_refused_while_afe_stale(rig):
    require(rig, "tare", "TareEngine (SW_design §9.5)")
    rig.act("afe", stall=True)
    rig.advance(600)
    g = rig.be.tare(2.0)
    stub_xfail(g, "Backend.tare")
    refused_now = not g.ok
    if not refused_now:
        with pytest.raises(AssertionError):
            drive(rig, rig.be.tare_engine, {}, timeout_ms=12_000)
    assert field(rig.be.status().tare, "tare_raw", "raw") is None


# ============================================================================================ load calibration
@pytest.mark.req("SW-CAL-005", "SW-CAL-006", "SW-CAL-007", "SW-CAL-008", "SW-CAL-009")
def test_load_calibration_zero_1kg_10kg_low_span(rig):
    require(rig, "cal_load_start", "LoadCalEngine (SW_design §9.4)")
    st = load_calibration(rig)
    rec = rig.be.calibrations.active_load()
    assert rec is not None, flatten(st)
    fit = rec["fit"]                                                    # R4 §6.4 record schema
    assert fit["k_n_per_count"] == pytest.approx(1.0 / CPN, rel=2e-3), fit   # N per count (twin 3 285 counts/N)
    assert fit["low_span"] is True, fit                                # 98 N < 20 % FS (SW-CAL-008)
    assert fit["f_span_n"] == pytest.approx(10 * G0, rel=1e-6)
    assert fit["status"] in ("PASS", "WARN"), fit                      # 3 points: linearity verified
    assert [round(float(field(pt, "mass_kg", "m_kg", default=-1)), 3) for pt in rec["points"]] in ([0.0, 1.0, 10.0], [-1, -1, -1])


# ============================================================================================ travel calibration
@pytest.mark.req("SW-CAL-001", "SW-CAL-002", "SW-CAL-003", "SW-CAL-004")
def test_travel_calibration_10_50_mm_measured(twin_exe, tmp_path, monkeypatch):
    """True mechanics 790 steps/mm (twin world), board 800: the operator's caliper readings (world distances,
    0.01 mm resolution) give spm ≈ 790; ACCEPT writes it to the board and saves the NVM record."""
    from twin_rig import Rig

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    scn = {"schema": "bird.bend.simscenario", "version": 1, "params": {"motion.steps_per_mm": 790.0},
           "world": {"load_offset_counts": OFFSET, "afe": {"noise_counts": NOISE}}}
    rig = Rig(twin_exe, tmp_path / "twin", scenario=scn, recordings=tmp_path / "rec")
    try:
        rig.connect()
        rig.advance(500)
        require(rig, "cal_travel_start", "TravelCalEngine (SW_design §9.3)")
        free_motion(rig)
        enable_home(rig)
        tc = rig.be.travel_cal
        ref: dict[str, float] = {}

        def x_mm() -> float:
            return rig.tw.act("query", what="world")["x_um_true"] / 1000.0

        def reference(st):  # noqa: ANN001, ANN202
            ref["x0"] = x_mm()                            # the operator zeroes the caliper here
            return None

        def enter_d1(st):  # noqa: ANN001, ANN202
            return fill(st, round(x_mm() - ref["x0"], 2), "d1", "mm", "dist")

        def enter_dtot(st):  # noqa: ANN001, ANN202
            return fill(st, round(x_mm() - ref["x0"], 2), "tot", "mm", "dist")

        g = tc.start()
        stub_xfail(g, "travel_cal.start")
        if g.needs_confirmation:                         # B5-18: start() started nothing, returned the CONFIRM gate
            g = tc.start(confirmed=True)                 # the operator confirms "no specimen mounted" (§9.3 CHECK)
        assert g.ok, g
        st = drive(rig, tc, {"REFERENCE": reference, "ENTER_D1": enter_d1, "ENTER_DTOT": enter_dtot},
                   timeout_ms=240_000)
        rig.advance(500)
        spm = rig.result(rig.be.config.read_all_async())["motion.steps_per_mm"]
        assert spm == pytest.approx(790.0, abs=0.5), flatten(st.result)            # 0.01 mm over 60 mm ≈ 0.13
        assert rig.rx_types().count(0x13) == 1                                     # SAVE_PARAMS once (ACCEPT)
        assert rig.be.calibrations.active_travel() is not None
    finally:
        rig.close()


# ============================================================================================ PC load limit
@pytest.mark.req("SAF-SW-001", "SAF-SW-002")
def test_pc_load_limit_stop_latency(rig):
    """Calibrated + tared; SW pull limit 50 N; spring 50 N/mm from 21 mm, MOVE to 60 mm at 2 mm/s: the first DATA
    frame with F > 50 N leads to STOP on the wire ≤ 50 ms (virtual time) after its last byte; FW STOPPED cause PC
    stop; SW trip PULL latched; motion further into tension refused."""
    require(rig, "cal_load_start", "LoadCalEngine (SW_design §9.4)")
    require(rig, "tare", "TareEngine (SW_design §9.5)")
    from bend_stand.core.model import LimitConfig

    load_calibration(rig, masses=(0.0, 1.0, 10.0))
    tare(rig, 2.0)
    issues = rig.be.limits.set(LimitConfig(pull_trip_n=50.0, push_trip_n=-50.0))
    assert not [i for i in issues if "ERROR" in str(i.severity)], issues
    rig.result(rig.be.limits.recheck_async())
    enable_home(rig)
    rig.act("specimen", kind="spring", k_n_per_mm=50.0, x_contact_um=world_x0(rig) + 21_000)
    t0 = rig.tw.now / 1000
    fut = rig.be.motion.move_to(60.0, speed_mm_s=2.0)
    rig.run_until(fut.done, 40_000)
    rig.advance(300)
    k = 1.0 / CPN
    tare_raw = field(rig.be.status().tare, "tare_raw", "raw")
    viol = next(w for w, d in data_frames(rig, t0) if k * (d["afe_raw"] - tare_raw) > 50.0)
    stop = next(w for w in rig.tw.wire_log
                if w["dir"] == "rx" and w["type"] == rc.CMD["STOP"] and w["first_us"] >= viol["last_us"])
    assert stop["first_us"] - viol["last_us"] <= 50_000, (viol, stop)
    s = rig.be.status().safety
    assert "PULL" in flatten(s), flatten(s)


# ============================================================================================ recording
@pytest.mark.req("SW-ACQ-001", "SW-ACQ-002", "SW-ACQ-004")
def test_recording_of_a_moving_test(rig, tmp_path):
    """Spring specimen, MOVE 0 → 30 mm at 5 mm/s while recording: data.csv holds exactly one D row per DATA frame
    sent by the FW during the recording (frame_seq, raw, setpoint, MOVING as on the wire), FW events as E rows,
    meta.json complete. SW load limits off (no-specimen mode, no calibration in this test); the FW limit stays."""
    be = rig.be
    free_motion(rig)
    enable_home(rig)
    rig.act("specimen", kind="spring", k_n_per_mm=20.0, x_contact_um=world_x0(rig) + 10_000, relax_pct=5.0,
            relax_tau_s=3.0)
    assert be.record_start().ok
    rig.advance(200)
    fut = be.motion.move_to(30.0, speed_mm_s=5.0)
    out = rig.result(fut, 20_000)
    assert "TARGET" in str(out), out
    rig.advance(1500)                                          # relaxation visible at standstill
    assert be.record_stop().ok
    rig.advance(100)
    folders = [p for p in (tmp_path / "rec").iterdir() if p.is_dir()]
    assert len(folders) == 1
    rows = [r for r in csv.reader(l for l in (folders[0] / "data.csv").read_text(encoding="utf-8").splitlines()
                                  if not l.startswith("#"))]
    head, body = rows[0], rows[1:]
    col = {c: i for i, c in enumerate(head)}
    d_rows = [r for r in body if r[col["row_type"]] == "D"]
    e_rows = [r for r in body if r[col["row_type"]] == "E"]
    seqs = [int(r[col["frame_seq"]]) for r in d_rows]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))                     # every frame exactly once
    wire = {d["frame_seq"]: d for _w, d in data_frames(rig)}
    assert set(seqs) <= set(wire)
    for r in d_rows:                                                              # raw / setpoint as sent
        d = wire[int(r[col["frame_seq"]])]
        assert int(r[col["raw"]]) == d["afe_raw"] and int(r[col["setpoint_um"]]) == d["setpoint_um"]
        assert int(r[col["moving"]]) == int("MOVING" in d["flags"])
    moving = [int(r[col["moving"]]) for r in d_rows]
    assert 1 in moving and moving[-1] == 0
    x_last = float(d_rows[-1][col["x_mm"]])
    assert x_last == pytest.approx(30.0, abs=0.002)
    raws = [int(r[col["raw"]]) for r in d_rows]
    assert max(raws) - OFFSET > 0.9 * CPN * 20.0 * 20.0                          # 20 mm into a 20 N/mm spring
    assert raws[-1] < max(raws)                                                    # relaxation at standstill
    assert any("MOVE_DONE" in r[col["event"]] for r in e_rows), e_rows[:5]
    meta = json.loads((folders[0] / "meta.json").read_text(encoding="utf-8"))
    integ = meta.get("integrity", {})
    assert integ.get("complete", True) is True and integ.get("rows_lost", 0) == 0, integ
