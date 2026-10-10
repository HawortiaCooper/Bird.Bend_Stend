"""M4 report — input errors, warnings, report-tab classification and rendering edge cases on **synthetic**
recordings (no simulator): ``data.csv`` + ``meta.json`` written by the test from known values, so every expected
number / text comes from the test, not from the report code (SW_design §11, §13.7–§13.9).

Verifies: SW-REP-001, SW-REP-002, SW-REP-003, SW-REP-004
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bend_stand.core import report as R
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import Bend3pGeometry, StepResult

K = 1 / 3285.0
TARE = 50_000.0
COLS = "row_type,t_us_u,t_dev_s,flags,status,raw,raw_state,setpoint_um,seq_lost,event"
CAL = {"created_utc": "2026-10-05T00:00:00Z", "file": "load_x.json",
       "points": [{"force_n": 0.0, "raw_mean": TARE}, {"force_n": 392.266, "raw_mean": TARE + 392.266 / K}],
       "fit": {"k_n_per_count": K, "status": "PASS"}}


def _row(t: int, f_n: float, *, x_um: int = 15_000, valid: bool = False, lost: int = 0, raw_state: int = 0) -> str:
    raw = TARE + f_n / K
    return f"D,{t},{t / 1e6:.6f},{1 if valid else 0},0,{raw:.3f},{raw_state},{x_um},{lost},"


def _write(folder: Path, rows: list[str], meta: dict[str, Any], *, header: str = COLS) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text("# bird.bend.data 1\n" + header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"schema": "bird.bend.recording", "schema_version": 1, **meta}),
                                      encoding="utf-8")
    return folder


def _window(exec_idx: int, uid: str, t0: int, t1: int, *, kind: str = "load", target: float | None = 100.0,
            tol: float | None = 2.0) -> dict[str, Any]:
    return {"exec_idx": exec_idx, "step_idx": exec_idx, "uid": uid, "loop_iters": [], "kind": kind, "label": uid,
            "target": target, "tol_n": tol, "t0_us_u": t0, "t1_us_u": t1, "t_reached_us_u": t0 - 500_000,
            "t_on_us_u": t0, "t_off_us_u": t1, "ramp": False, "k_est_n_mm": 50.0, "discarded": False, "reason": ""}


def _seq_recording(tmp: Path, **meta_kw: Any) -> Path:
    """Two load windows (100 N for 1 s, 150 N for only 0.25 s of a 1 s window → INCOMPLETE), a discarded window,
    a NOT_REACHED result without a window, one lost frame, event rows."""
    rows, t = [], 0
    for i in range(200):                                         # 2.5 s at 80 SPS
        f = 100.0 if t < 1_750_000 else 150.0
        rows.append(_row(t, f, x_um=15_000 + (2000 if f == 100.0 else 3000), valid=500_000 <= t <= 1_500_000 or
                         2_000_000 <= t <= 2_250_000, lost=1 if i == 150 else 0))
        t += 12_500
    rows.insert(3, "E,25000,0.025000,,,,,,,SEQ_START: name=s")
    rows.insert(4, "E,,,,,,,,,STOP: gui")
    rows.insert(5, "X,1,2,3")                                    # short / foreign line: skipped
    rows.insert(6, "Q,0,0,0,0,0,0,0,0,")                          # unknown row type: skipped
    nr = StepResult(2, 2, "l2", "200 N", "load", (), 200.0, "N", 2.0, ("NOT_REACHED",), text="bound")
    run = {"sequence": {"name": "stair"}, "state": "STOPPED", "end_reason": "NOT_REACHED", "message": "bound",
           "start_utc": "2026-10-05T10:00:00Z", "k_est_final_n_mm": 50.0, "x_zero_mm": 10.0, "travel_ref": "test",
           "x_off_mm": 10.0,
           "windows": [_window(0, "l0", 500_000, 1_500_000), _window(1, "l1", 2_000_000, 3_000_000, target=150.0),
                       dict(_window(1, "l1", 1_600_000, 1_700_000), discarded=True)],
           "results": [R.result_to_dict(nr)],
           "scale_log": [{"t_us_u": 0, "k": K, "tare_raw": TARE, "ok": True}]}
    meta = {"start_utc": "2026-10-05T10:00:00Z", "stop_utc": "2026-10-05T10:00:03Z",
            "snapshot": {"calibration": CAL, "tare": {"tare_raw": TARE}, "limits": {"pull_trip_n": 500.0}},
            "marks_final": {"specimen": "S", "number": "7", "custom": [["lot", "A"], {"key": "k2", "value": "v2"},
                                                                       ["bad"], 5]},
            "integrity": {"complete": True}, "sequence_runs": [run]}
    meta.update(meta_kw)
    return _write(tmp / "rec", rows, meta)


# ================================================================================================ input errors
@pytest.mark.req("SW-REP-003")
def test_recording_input_errors(tmp_path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    with pytest.raises(FileFormatError, match="meta.json"):
        R.Recording(d)                                                           # no sidecar
    (d / "meta.json").write_text(json.dumps({"schema": "bird.bend.other"}), encoding="utf-8")
    with pytest.raises(FileFormatError, match="not a bird.bend.recording"):
        R.Recording(d)
    (d / "meta.json").write_text(json.dumps({"schema": "bird.bend.recording"}), encoding="utf-8")
    with pytest.raises(FileFormatError, match="data.csv"):
        R.Recording(d)                                                           # no data file
    (d / "data.csv").write_text("# only a comment\n\n", encoding="utf-8")
    with pytest.raises(FileFormatError, match="no column header"):
        R.Recording(d)
    (d / "data.csv").write_text("row_type,t_us_u\nD,1\n", encoding="utf-8")
    with pytest.raises(FileFormatError, match="columns missing"):
        R.Recording(d)
    with pytest.raises(FileFormatError, match="fit.k_n_per_count"):
        R.build_report(_seq_recording(tmp_path), cal={"fit": {}})
    with pytest.raises(FileFormatError, match="calibration file"):
        R.build_report(_seq_recording(tmp_path), cal=str(tmp_path / "missing.json"))
    assert R.main([str(tmp_path / "rec"), "--cal", str(tmp_path / "missing.json")]) == 2


@pytest.mark.req("SW-REP-001", "SW-REP-002")
def test_synthetic_sequence_report_numbers_warnings_and_html(tmp_path) -> None:
    rec_dir = _seq_recording(tmp_path)
    rec = R.Recording(rec_dir)
    assert rec.t.size == 200 and rec.lost == 1 and len(rec.events) == 2 and rec.events[1][0] is None
    p = R.build_report(rec_dir, now_utc="2026-10-05T11:00:00Z")
    doc = json.loads(Path(p.json).read_text(encoding="utf-8"))
    res = R.load_result(rec_dir)
    assert [r.uid for r in res.results] == ["l0", "l1", "l2"]
    r0, r1, r2 = res.results
    assert r0.n == 81 and r0.f_mean_n == pytest.approx(100.0, abs=1e-6) and "ON_TARGET" in r0.flags
    assert r0.x_mean_mm == pytest.approx(17.0) and r0.t_reached_s == pytest.approx(0.0)
    assert "INCOMPLETE" in r1.flags and r1.f_mean_n == pytest.approx(150.0, abs=1e-6)
    assert r2.flags == ("NOT_REACHED",) and r2.n == 0 and math.isnan(r2.f_mean_n)
    w = res.warnings
    for needle in ("1 INCOMPLETE window(s)", "1 step(s) NOT_REACHED", "1 frame(s) lost", "ended STOPPED (NOT_REACHED)"):
        assert any(needle in x for x in w), (needle, w)
    assert not any("calibration" in x and "no load" in x for x in w)
    assert doc["summary"] == {"rows": 200, "frames_lost": 1, "results": 3, "incomplete": 1, "not_reached": 1,
                              "f_max_n": pytest.approx(150.0), "f_min_n": pytest.approx(100.0)}
    assert doc["calibration"]["file"] == "load_x.json" and doc["created_utc"] == "2026-10-05T11:00:00Z"
    html = Path(p.html).read_text(encoding="utf-8")
    assert "<th class='l'>lot</th><td>A</td>" in html and "<th class='l'>k2</th><td>v2</td>" in html
    assert "bad" not in html.split("Snapshot")[0].split("Marks")[1] and "pull_trip_n=500.0" in html
    assert "SEQ_START: name=s" in html and "STOP: gui" in html
    info = R.list_recordings(tmp_path)[0]
    assert (info.status, info.sequence_name, info.duration_s, info.has_report) == ("PARTIAL", "stair", 3.0, True)
    assert info.marks == "S, 7"


@pytest.mark.req("SW-REP-004", "SW-REP-003")
def test_bend3p_from_the_session_and_with_too_few_points(tmp_path) -> None:
    rec_dir = _seq_recording(tmp_path, snapshot={
        "calibration": CAL, "tare": {"tare_raw": TARE},
        "session": {"bend3p": {"span_mm": 100.0, "width_mm": 20.0, "thickness_mm": 5.0}}})
    R.build_report(rec_dir)                                                      # geometry from the session snapshot
    d = json.loads((rec_dir / "report.json").read_text(encoding="utf-8"))["bend3p"]
    assert d["span_mm"] == 100.0 and len(d["rows"]) == 2                         # the NaN (NOT_REACHED) row skipped
    assert d["rows"][0]["deflection_mm"] == pytest.approx(7.0)                   # x_mean 17 − x_off 10
    assert d["e_f_mpa"] == pytest.approx(100.0 ** 3 * 50.0 / (4 * 20.0 * 125.0))   # 50 N / 1 mm between the windows
    one = _write(tmp_path / "one", [_row(i * 12_500, 100.0, valid=True) for i in range(100)],
                 {"snapshot": {"calibration": CAL, "tare": {"tare_raw": TARE}},
                  "sequence_runs": [{"windows": [_window(0, "a", 0, 1_000_000)], "results": [], "state": "FINISHED",
                                     "x_off_mm": 10.0}], "integrity": {"complete": True}})
    R.build_report(one, bend3p=Bend3pGeometry(100.0, 20.0, 5.0))
    b = json.loads((one / "report.json").read_text(encoding="utf-8"))["bend3p"]
    assert len(b["rows"]) == 1 and b["e_f_mpa"] is None                         # E_f needs ≥ 2 points


@pytest.mark.req("SW-REP-001")
def test_manual_recording_without_calibration_runs_or_events(tmp_path) -> None:
    rows = [_row(i * 12_500, 0.0) for i in range(40)]
    d = _write(tmp_path / "m", rows, {"snapshot": {"x_zero_mm": 5.0}, "no_specimen_mode": True,
                                      "integrity": {"complete": False, "rows_lost": 3}})
    R.build_report(d)
    res = R.load_result(d)
    assert res.results == () and res.calibration is None
    w = res.warnings
    for needle in ("no load calibration", "no-specimen mode was on", "recording incomplete"):
        assert any(needle in x for x in w), (needle, w)
    html = (d / "report.html").read_text(encoding="utf-8")
    assert "no data" in html and "<h2>Events</h2>" not in html and "SW limits" not in html
    doc = json.loads((d / "report.json").read_text(encoding="utf-8"))
    assert doc["summary"]["f_max_n"] is None and doc["tare_raw"] is None


@pytest.mark.req("SW-REP-001")
def test_push_and_extrapolation_warnings_with_a_calibration_record(tmp_path) -> None:
    rows = [_row(i * 12_500, -50.0 if i < 20 else 1500.0, valid=True) for i in range(40)]
    d = _write(tmp_path / "p", rows, {"snapshot": {"tare": {"tare_raw": TARE}},
                                      "sequence_runs": [{"windows": [], "results": [], "state": "FINISHED"}]})
    R.build_report(d, cal=dict(CAL))                                             # record (mapping), not a file
    w = R.load_result(d).warnings
    assert any("push forces tension-calibrated" in x for x in w)
    assert any("beyond 3 × the largest calibration force" in x for x in w)
    assert "re-calculated with another calibration" in w


# ================================================================================================ report tab
@pytest.mark.req("SW-REP-001")
def test_list_recordings_classification(tmp_path) -> None:
    def meta(name: str, m: dict[str, Any]) -> None:
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps(m), encoding="utf-8")

    base = {"schema": "bird.bend.recording"}
    meta("a_failed", {**base, "start_utc": "2026-10-05T10:00:01Z", "integrity": {"failures": ["ENOSPC"]}})
    meta("b_complete", {**base, "start_utc": "2026-10-05T10:00:02Z", "stop_utc": "2026-10-05T10:00:12Z",
                        "integrity": {"complete": True}, "sequence_runs": [{"state": "FINISHED", "sequence": {}}]})
    meta("c_started", {**base, "start_utc": "2026-10-05T10:00:03Z", "stop_utc": "not a date",
                       "sequence_start": {"sequence": {"name": "named"}}})
    meta("d_unnamed", {**base, "start_utc": "2026-10-05T10:00:04Z", "sequence_start": {"sequence": {}}})
    meta("e_other", {"schema": "bird.bend.session"})
    meta("f_list", [1, 2])
    (tmp_path / "g_file.txt").write_text("x", encoding="utf-8")
    infos = R.list_recordings(tmp_path)
    assert [Path(i.folder).name for i in infos] == ["d_unnamed", "c_started", "b_complete", "a_failed"]
    by = {Path(i.folder).name: i for i in infos}
    assert by["a_failed"].status == "FAILED" and by["a_failed"].sequence_name is None
    assert by["b_complete"].status == "COMPLETE" and by["b_complete"].sequence_name == "(unnamed)"
    assert by["b_complete"].duration_s == 10.0
    assert by["c_started"].status == "PARTIAL" and by["c_started"].sequence_name == "named"
    assert by["c_started"].duration_s is None and by["d_unnamed"].sequence_name == "(unnamed)"


@pytest.mark.req("SW-REP-001")
def test_load_result_refuses_other_and_newer_files(tmp_path) -> None:
    (tmp_path / "report.json").write_text(json.dumps({"schema": "bird.bend.recording"}), encoding="utf-8")
    with pytest.raises(FileFormatError, match="not a bird.bend.report"):
        R.load_result(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"schema": "bird.bend.report", "schema_version": 99}),
                                          encoding="utf-8")
    with pytest.raises(FileFormatError, match="newer SW version"):
        R.load_result(tmp_path)


# ================================================================================================ helpers
@pytest.mark.req("SW-REP-001")
def test_json_and_svg_helpers() -> None:
    assert R._js(np.float64(1.5)) == 1.5 and R._js(np.int64(3)) == 3 and R._js(float("inf")) is None  # noqa: SLF001
    assert R._js({"a": (1.0, float("nan"))}) == {"a": [1.0, None]}  # noqa: SLF001
    r = R.result_from_dict({"exec_idx": 1, "step_idx": 0, "uid": "u", "label": "", "kind": "travel",
                            "f_mean_n": None, "window_s": [1, 2]})
    assert r.uid == "u" and math.isnan(r.f_mean_n) and r.window_s == (1.0, 2.0) and r.flags == ()
    assert R._nice_ticks(1.0, 1.0) == [1.0] and R._nice_ticks(float("nan"), 1.0) == []  # noqa: SLF001
    assert R._nice_ticks(0.0, 10.0) == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]  # noqa: SLF001
    assert R._fmt(None) == "–" and R._fmt("<a>") == "&lt;a&gt;" and R._fmt(3) == "3"  # noqa: SLF001
    assert R._fmt(float("nan")) == "–" and R._fmt(123456.0) == "1.235e+05"  # noqa: SLF001
    empty = R.svg_chart(np.array([]), np.array([]), title="T", x_label="x", y_label="y")
    assert "T: no data" in empty and "<polyline" not in empty
    flat = R.svg_chart(np.array([1.0, 1.0]), np.array([2.0, 2.0]), title="F", x_label="x", y_label="y",
                       shade=[(5.0, 6.0), (0.5, 1.5)], points=[(1.0, 2.0, "p"), (float("nan"), 1.0, "q")])
    assert flat.count("class=\"w\"") == 1 and flat.count("<circle") == 1          # shade outside / NaN point dropped
    x = np.arange(12_000, dtype=float)
    big = R.svg_chart(x, x, title="B", x_label="x", y_label="y")
    assert big.split("points=\"")[1].count(",") <= R.MAX_POINTS                  # decimated to ≤ 5000 points
