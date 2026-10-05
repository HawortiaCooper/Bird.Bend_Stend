"""M4 report (raw CSV + JSON + HTML), offline reproduction with another calibration / tare, 3-point bend, report-tab
API; sequencer facade (files, generators, path) and the MC3-5 topic.

Verifies: SW-REP-001, SW-REP-002, SW-REP-003, SW-REP-004, SW-SEQF-001, SW-WIZ-001, SW-SCH-001
"""
from __future__ import annotations

import json
import math
from html.parser import HTMLParser
from pathlib import Path

import pytest
from seq_rig import CAL, K_SIM, load_seq, ready, run_to_end, start

from bend_stand.core import api
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import Bend3pGeometry
from bend_stand.core.report import build_report, list_recordings, load_result, main, result_from_dict, result_to_dict


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:  # noqa: ANN001
        self.tags.append(tag)

    def handle_data(self, data) -> None:  # noqa: ANN001
        self.text.append(data)


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> tuple[str, list]:
    tmp = tmp_path_factory.mktemp("rep")
    be = ready(tmp)
    try:
        be.marks.set(api.TestMarks("wing bracket", "17", "op", "n", (("lot", "A1"),)))
        start(be, load_seq(100.0, 150.0))
        st = run_to_end(be)
        assert st.state == "FINISHED"
        return st.recording_folder, list(be.sequencer.results())
    finally:
        be.shutdown()


@pytest.mark.req("SW-REP-001", "SW-REP-002", "SW-REP-003")
def test_three_files_and_regenerated_numbers_equal_live(run_dir, tmp_path) -> None:
    folder, live = run_dir
    p = Path(folder)
    assert (p / "data.csv").is_file() and (p / "meta.json").is_file()
    assert (p / "report.json").is_file() and (p / "report.html").is_file()
    meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    run = meta["sequence_runs"][0]
    assert run["state"] == "FINISHED" and run["sequence"]["schema"] == "bird.bend.sequence"
    assert len(run["windows"]) == 2 and run["scale_log"] and "k_est_final_n_mm" in run
    assert run["events"][0]["name"] == "SEQ_START" and all(e["t_us_u"] is not None for e in run["events"])  # OBS-M4-03
    res = load_result(folder)
    assert len(res.results) == 2
    for a, b in zip(live, res.results, strict=True):
        for f in ("n", "f_mean_n", "f_std_n", "f_se_n", "f_drift_n", "x_mean_mm", "raw_mean", "raw_std", "flags"):
            assert getattr(a, f) == getattr(b, f), f                     # exactly reproduced (SW-REP-003)
    out = tmp_path / "again"
    build_report(folder, out_dir=out)
    again = load_result(out)
    assert [r.f_mean_n for r in again.results] == [r.f_mean_n for r in res.results]
    assert res.marks["specimen"] == "wing bracket" and res.calibration["k_n_per_count"] == pytest.approx(K_SIM)
    assert res.summary["results"] == 2 and res.summary["frames_lost"] == 0


@pytest.mark.req("SW-REP-001")
def test_html_contents_checklist(run_dir) -> None:
    folder, _live = run_dir
    text = (Path(folder) / "report.html").read_text(encoding="utf-8")
    t = _Tags()
    t.feed(text)
    assert t.tags.count("svg") == 2 and "table" in t.tags and "polyline" in t.tags
    body = " ".join(t.text)
    for needle in ("Marks", "wing bracket", "lot", "A1", "Snapshot", "calibration K", "tare raw", "Warnings",
                   "F–x", "F–t", "Step results", "ON_TARGET", "100 N", "150 N", "Events", "SEQ_START"):
        assert needle in body, needle
    assert "<script" not in text and "http://" not in text.replace("http://www.w3.org/2000/svg", "")


@pytest.mark.req("SW-REP-003")
def test_reapplied_tare_and_calibration(run_dir, tmp_path) -> None:
    folder, _live = run_dir
    base = load_result(folder)
    d_tare = 3285.0                                                  # +1 N worth of counts
    build_report(folder, tare=base.tare_raw + d_tare, out_dir=tmp_path / "t")
    r = load_result(tmp_path / "t")
    for a, b in zip(base.results, r.results, strict=True):
        assert b.f_mean_n == pytest.approx(a.f_mean_n - K_SIM * d_tare, abs=1e-9)
        assert b.raw_mean == a.raw_mean and b.x_mean_mm == a.x_mean_mm
    assert "re-calculated with another tare" in r.warnings
    cal2 = json.loads(json.dumps(CAL))
    cal2["fit"]["k_n_per_count"] = 2 * K_SIM
    cal2["fit"]["low_span"] = True
    cf = tmp_path / "cal.json"
    cf.write_text(json.dumps(cal2), encoding="utf-8")
    build_report(folder, cal=str(cf), out_dir=tmp_path / "c")
    rc = load_result(tmp_path / "c")
    assert rc.results[0].f_mean_n == pytest.approx(2 * base.results[0].f_mean_n, rel=1e-6, abs=1e-6)
    assert any("LOW_SPAN" in w for w in rc.warnings) and "re-calculated with another calibration" in rc.warnings
    assert "NOT_ON_TARGET" in rc.results[0].flags
    assert main([folder, "--tare-raw", str(base.tare_raw), "--out", str(tmp_path / "cli")]) == 0
    assert [x.f_mean_n for x in load_result(tmp_path / "cli").results] == pytest.approx(
        [x.f_mean_n for x in base.results])
    assert main([str(tmp_path / "nothing")]) == 2


@pytest.mark.req("SW-REP-004")
def test_three_point_bend_outputs(run_dir, tmp_path) -> None:
    folder, _live = run_dir
    g = Bend3pGeometry(100.0, 20.0, 5.0)
    build_report(folder, bend3p=g, out_dir=tmp_path)
    d = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    b3 = d["bend3p"]
    assert b3["span_mm"] == 100.0 and len(b3["rows"]) == 2
    row = b3["rows"][0]
    assert row["sigma_mpa"] == pytest.approx(3 * row["f_n"] * 100.0 / (2 * 20.0 * 25.0))
    assert row["eps"] == pytest.approx(6 * row["deflection_mm"] * 5.0 / 100.0 ** 2)
    m = (b3["rows"][1]["f_n"] - b3["rows"][0]["f_n"]) / (b3["rows"][1]["deflection_mm"] - b3["rows"][0]["deflection_mm"])
    assert b3["e_f_mpa"] == pytest.approx(100.0 ** 3 * m / (4 * 20.0 * 125.0), rel=1e-6)
    assert abs(m - 50.0) < 3.0                                       # the simulator specimen: 50 N/mm
    assert "3-point bend" in (tmp_path / "report.html").read_text(encoding="utf-8")
    build_report(folder, bend3p=False, out_dir=tmp_path / "off")
    assert json.loads((tmp_path / "off" / "report.json").read_text(encoding="utf-8"))["bend3p"] is None


@pytest.mark.req("SW-REP-001")
def test_list_recordings_load_result_errors(run_dir, tmp_path) -> None:
    folder, _live = run_dir
    root = Path(folder).parent
    infos = list_recordings(root)
    me = next(i for i in infos if i.folder == folder)
    assert me.status == "COMPLETE" and me.has_report and me.sequence_name == "test" and me.duration_s > 0
    assert "wing bracket" in me.marks
    assert list_recordings(tmp_path / "none") == []
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "meta.json").write_text("{", encoding="utf-8")
    assert list_recordings(tmp_path) == []
    with pytest.raises(FileFormatError):
        load_result(tmp_path / "junk")
    with pytest.raises(FileFormatError):
        build_report(tmp_path / "junk")
    r = load_result(folder).results[0]
    assert result_from_dict(json.loads(json.dumps(result_to_dict(r)))) == r or math.isnan(r.f_end_n)


@pytest.mark.req("SW-SEQF-001", "SW-WIZ-001", "SW-SCH-001", "SW-REP-001")
def test_sequencer_and_report_facade(tmp_path) -> None:
    be = ready(tmp_path, contact_mm=None)
    try:
        sq = be.sequencer
        assert isinstance(sq, api.SequencerAPI) and isinstance(be.reports, api.ReportAPI)
        seq = sq.new()
        assert seq.travel_ref == "test" and seq.k_est_n_mm == 50.0 and not seq.steps
        blk = sq.generate("staircase", {"start": 0.0, "end": 2.0, "by": "count", "count": 3})
        seq.insert_block(blk)
        p = tmp_path / "s.bbseq.json"
        sq.save(seq, str(p))
        back = sq.load(str(p))
        assert back == seq and sq.last_load_warnings == []
        cur = back
        (tmp_path / "bad.bbseq.json").write_text("{}", encoding="utf-8")
        with pytest.raises(FileFormatError):
            cur = sq.load(str(tmp_path / "bad.bbseq.json"))
        assert cur is back                                           # SW-SEQF-001: the current sequence stays
        assert sq.validate(seq) == []
        plan = sq.expand(seq)
        assert len(plan) == 3 and plan.steps[0].x_start_mm == pytest.approx(0.0, abs=1e-3)
        pts = sq.planned_path(seq)
        assert [round(pt.x_mm, 3) for pt in pts] == [0.0, 0.0, 1.0, 2.0] and sq.planned_path(plan) == pts
        sc = sq.generator_schemas()
        assert sc["staircase"].fields[0].name == "kind"
        assert any(f.max == pytest.approx(280.0) for f in sc["linear_ramp"].fields if f.name == "x1_mm")
        with pytest.raises(ValueError):
            sq.generate("staircase", speed_mm_s=1e6)
        assert sq.status().state == "IDLE" and sq.results() == ()
        fut = be.reports.build_async(str(tmp_path / "missing"))
        be.test_hooks.advance(5)
        assert isinstance(fut.exception(), FileFormatError)
        assert be.reports.list_recordings(str(tmp_path)) == []
        assert "safety.trip_cleared" in api.TOPICS                   # MC3-5
    finally:
        be.shutdown()
